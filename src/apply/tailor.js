import { readFile, copyFile, access } from 'fs/promises';
import { Ollama } from 'ollama';
import { streamWithWatchdog, withRetry } from '../core/stream.js';

const ollama = new Ollama({ host: 'http://127.0.0.1:11434' });

const DEFAULT_RESUME_PATH = new URL('../../base-resume.json', import.meta.url);
const LEGACY_RESUME_PATH = new URL('../../data/base-resume.json', import.meta.url);

const resumeCache = new Map();

export function getResumePath(profile = null) {
  const name = profile ? `base-resume.${profile}.json` : 'base-resume.json';
  return new URL(`../../${name}`, import.meta.url).pathname;
}

async function ensureResumeAtRoot() {
  try {
    await access(DEFAULT_RESUME_PATH);
    return;
  } catch {}
  try {
    await access(LEGACY_RESUME_PATH);
    await copyFile(LEGACY_RESUME_PATH, DEFAULT_RESUME_PATH);
    process.stderr.write('[resume] migrated data/base-resume.json -> base-resume.json (project root)\n');
  } catch {}
}

function applyEnvOverrides(resume) {
  const env = process.env;
  return {
    ...resume,
    name: env.APPLICANT_NAME || resume.name,
    email: env.APPLICANT_EMAIL || resume.email,
    phone: env.APPLICANT_PHONE || resume.phone,
    linkedin: env.APPLICANT_LINKEDIN || resume.linkedin,
    github: env.APPLICANT_GITHUB || resume.github,
    website: env.APPLICANT_WEBSITE || resume.website,
    location: env.APPLICANT_LOCATION || resume.location,
  };
}

export async function loadBaseResume(profile = null) {
  const key = profile || '__default__';
  if (resumeCache.has(key)) return resumeCache.get(key);
  if (!profile) await ensureResumeAtRoot();
  const path = new URL(`../../${profile ? `base-resume.${profile}.json` : 'base-resume.json'}`, import.meta.url);
  const raw = await readFile(path, 'utf-8');
  const result = applyEnvOverrides(JSON.parse(raw));
  resumeCache.set(key, result);
  return result;
}

export function resetResumeCache() {
  resumeCache.clear();
}

function compactResume(resume) {
  return {
    summary: resume.summary,
    experience: (resume.experience || []).map((e, i) => ({
      i,
      title: e.title,
      company: e.company,
      bullets: e.bullets || [],
    })),
    skills: resume.skills || [],
  };
}

function buildPrompt(resume, analysis) {
  return `You are a resume optimization expert. Rewrite the summary and reorder existing bullets to match this job. NEVER invent experience.

ROLE: ${analysis.role_title} at ${analysis.company_name}
TONE: ${analysis.tone}
KEYWORDS TO EMPHASIZE: ${analysis.keywords.join(', ')}

TOP REQUIREMENTS:
${analysis.requirements.slice(0, 8).join('\n')}

MASTER RESUME (relevant fields only):
${JSON.stringify(compactResume(resume))}

Return ONLY a JSON object with this exact shape (no markdown):
{
  "summary": "<rewritten summary, 2-3 sentences>",
  "experience": [
    { "i": <index from input>, "bullets": [<reordered existing bullets, most relevant first>] }
  ],
  "relevance_notes": "<1-2 sentences on what you emphasized>"
}`;
}

function mergeTailored(baseResume, tailored) {
  const merged = { ...baseResume };
  if (tailored.summary) merged.summary = tailored.summary;
  if (Array.isArray(tailored.experience)) {
    merged.experience = baseResume.experience.map((orig, idx) => {
      const override = tailored.experience.find(e => e.i === idx);
      if (override && Array.isArray(override.bullets) && override.bullets.length) {
        return { ...orig, bullets: override.bullets };
      }
      return orig;
    });
  }
  if (tailored.relevance_notes) merged.relevance_notes = tailored.relevance_notes;
  return merged;
}

export async function tailor(analysis, { model = 'gemma4:e2b', profile = null } = {}) {
  const resume = await loadBaseResume(profile);
  const prompt = buildPrompt(resume, analysis);

  process.stderr.write(`[tailor] model=${model}\n`);
  const result = await withRetry(async () => {
    const response = await ollama.chat({
      model,
      messages: [{ role: 'user', content: prompt }],
      stream: true,
      think: false,
      options: { num_predict: 2048 },
    });
    return streamWithWatchdog(response, 'tailor');
  }, { label: 'tailor' });

  const cleaned = result.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  const jsonMatch = cleaned.match(/\{[\s\S]*\}/);
  if (!jsonMatch) {
    throw new Error(`Failed to extract JSON from tailor response (got ${result.length} chars, ${cleaned.length} after stripping think tags). First 300 chars: ${result.slice(0, 300)}`);
  }

  const tailored = JSON.parse(jsonMatch[0]);
  return mergeTailored(resume, tailored);
}
