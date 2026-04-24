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

function normalize(parsed) {
  const toArray = v => Array.isArray(v) ? v.filter(x => typeof x === 'string' && x.trim()) : [];
  const toStr = (v, fallback = '') => typeof v === 'string' && v.trim() ? v.trim() : fallback;
  return {
    requirements: toArray(parsed.requirements),
    nice_to_haves: toArray(parsed.nice_to_haves),
    keywords: toArray(parsed.keywords),
    company_name: toStr(parsed.company_name, 'Unknown'),
    role_title: toStr(parsed.role_title, 'Unknown Role'),
    tone: toStr(parsed.tone, 'formal'),
  };
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

  return normalized;
}
