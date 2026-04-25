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

function tokenize(s) {
  return new Set((s || '').toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/).filter(Boolean));
}

function jaccardSimilarity(a, b) {
  const ta = tokenize(a);
  const tb = tokenize(b);
  if (!ta.size && !tb.size) return 1;
  let intersection = 0;
  for (const t of ta) if (tb.has(t)) intersection++;
  return intersection / (ta.size + tb.size - intersection);
}

function maxSimilarity(proposed, originals) {
  return originals.reduce((max, orig) => Math.max(max, jaccardSimilarity(proposed, orig)), 0);
}

export function validateTailoredBullets(originalBullets, proposedBullets, label = '') {
  const accepted = [];
  const rejected = [];
  for (const bullet of proposedBullets) {
    const sim = maxSimilarity(bullet, originalBullets);
    if (sim >= 0.6) {
      accepted.push(bullet);
    } else {
      process.stderr.write(`[tailor] rejected bullet${label ? ` (${label})` : ''}: "${bullet.slice(0, 80)}" (max similarity ${sim.toFixed(2)} < 0.6)\n`);
      rejected.push(bullet);
    }
  }
  return { accepted, rejected };
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
    projects: (resume.projects || []).map((p, i) => ({
      i,
      name: p.name,
      highlights: p.highlights || [],
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
  "projects": [
    { "i": <index from input>, "highlights": [<reordered existing highlights, most relevant first>] }
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
        const { accepted } = validateTailoredBullets(orig.bullets, override.bullets, orig.company);
        if (accepted.length) return { ...orig, bullets: accepted };
        process.stderr.write(`[tailor] all bullets rejected for ${orig.company} — keeping original order\n`);
      }
      return orig;
    });
  }

  if (Array.isArray(tailored.projects) && Array.isArray(baseResume.projects)) {
    merged.projects = baseResume.projects.map((orig, idx) => {
      const override = tailored.projects.find(p => p.i === idx);
      if (override && Array.isArray(override.highlights) && override.highlights.length) {
        const { accepted } = validateTailoredBullets(orig.highlights, override.highlights, orig.name);
        if (accepted.length) return { ...orig, highlights: accepted };
        process.stderr.write(`[tailor] all highlights rejected for project ${orig.name} — keeping original order\n`);
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
