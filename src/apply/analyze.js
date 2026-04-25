import { Ollama } from 'ollama';
import { streamWithWatchdog, withRetry } from '../core/stream.js';

const ollama = new Ollama({ host: 'http://127.0.0.1:11434' });

const SYSTEM_PROMPT = `You are a job posting analyzer. Extract structured data from the job description provided.
Return ONLY valid JSON with this exact schema:
{
  "requirements": ["list of hard requirements"],
  "nice_to_haves": ["list of preferred/bonus qualifications"],
  "keywords": ["important technical and domain keywords"],
  "company_name": "company name",
  "role_title": "job title",
  "tone": "formal|casual|technical (the overall tone of the posting)"
}
No markdown, no explanation — just the JSON object.`;

const RETRY_PROMPT = `You are a job posting analyzer. The previous extraction missed the job title or company name.
Extract ONLY these two fields from the job description:
- company_name: the exact name of the hiring company (look for "About [Company]", "at [Company]", the domain, or the posting header)
- role_title: the exact job title as written in the posting header or first sentence

Return ONLY valid JSON: { "company_name": "...", "role_title": "..." }
No markdown, no explanation.`;

const UNKNOWN_VALUES = new Set(['unknown', 'unknown role', '', 'n/a', 'not specified']);

function isUnknown(s) {
  return UNKNOWN_VALUES.has((s || '').toLowerCase().trim());
}

function normalize(parsed) {
  const toArray = v => Array.isArray(v) ? v.filter(x => typeof x === 'string' && x.trim()) : [];
  const toStr = (v, fallback = '') => typeof v === 'string' && v.trim() ? v.trim() : fallback;
  return {
    requirements: toArray(parsed.requirements),
    nice_to_haves: toArray(parsed.nice_to_haves),
    keywords: toArray(parsed.keywords),
    company_name: toStr(parsed.company_name, ''),
    role_title: toStr(parsed.role_title, ''),
    tone: toStr(parsed.tone, 'formal'),
  };
}

async function retryExtractTitleCompany(description) {
  const response = await ollama.chat({
    model: 'qwen2.5-coder:7b',
    messages: [
      { role: 'system', content: RETRY_PROMPT },
      { role: 'user', content: description.slice(0, 3000) },
    ],
    stream: true,
    options: { num_predict: 256 },
  });
  const raw = await streamWithWatchdog(response, 'analyze-retry');
  const cleaned = raw.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  const match = cleaned.match(/\{[\s\S]*\}/);
  if (!match) return null;
  try { return JSON.parse(match[0]); } catch { return null; }
}

export async function analyze(description) {
  const result = await withRetry(async () => {
    const response = await ollama.chat({
      model: 'qwen2.5-coder:7b',
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: description },
      ],
      stream: true,
      options: { num_predict: 1024 },
    });
    return streamWithWatchdog(response, 'analyze');
  }, { label: 'analyze' });

  const cleaned = result.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  const jsonMatch = cleaned.match(/\{[\s\S]*\}/);
  if (!jsonMatch) throw new Error(`Failed to extract JSON from analysis response. First 300 chars: ${result.slice(0, 300)}`);

  const parsed = JSON.parse(jsonMatch[0]);
  const normalized = normalize(parsed);

  if (!normalized.requirements.length && !normalized.keywords.length) {
    throw new Error('Analysis produced no requirements or keywords — try re-running.');
  }

  if (isUnknown(normalized.role_title) || isUnknown(normalized.company_name)) {
    process.stderr.write('[analyze] role_title or company_name unclear — retrying extraction\n');
    const retry = await retryExtractTitleCompany(description).catch(() => null);
    if (retry) {
      if (!isUnknown(retry.role_title)) normalized.role_title = retry.role_title.trim();
      if (!isUnknown(retry.company_name)) normalized.company_name = retry.company_name.trim();
    }
    if (isUnknown(normalized.role_title)) {
      throw new Error(
        `Could not extract job title from posting. ` +
        `Check that the URL points to a single job listing, not a search results page. ` +
        `Run with a different URL or set role_title manually.`
      );
    }
    if (isUnknown(normalized.company_name)) {
      throw new Error(
        `Could not extract company name from posting. ` +
        `Check the job URL or add the company name to the posting text.`
      );
    }
  }

  return normalized;
}
