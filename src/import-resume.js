import { readFile, writeFile, copyFile, access } from 'node:fs/promises';
import { extname, resolve } from 'node:path';
import { Ollama } from 'ollama';
import { streamWithWatchdog, withRetry } from './_stream.js';
import { RESUME_PATH, resetResumeCache } from './tailor.js';
import { askYesNo } from './prompt.js';

const ollama = new Ollama({ host: 'http://127.0.0.1:11434' });

async function extractText(filePath) {
  const ext = extname(filePath).toLowerCase();
  if (ext === '.pdf') {
    const { default: pdfParse } = await import('pdf-parse/lib/pdf-parse.js');
    const buf = await readFile(filePath);
    const data = await pdfParse(buf);
    return data.text;
  }
  if (ext === '.docx') {
    const mammoth = await import('mammoth');
    const result = await mammoth.extractRawText({ path: filePath });
    return result.value;
  }
  if (ext === '.txt' || ext === '.md') {
    return await readFile(filePath, 'utf-8');
  }
  throw new Error(`Unsupported file type: ${ext}. Use .pdf, .docx, .txt, or .md.`);
}

function buildPrompt(text) {
  return `You convert a resume into strict JSON. Use ONLY information present in the source. If a field is missing, use an empty string or empty array. Never invent data.

Return ONLY a JSON object with this shape (no markdown, no prose):
{
  "name": "",
  "email": "",
  "phone": "",
  "linkedin": "",
  "github": "",
  "website": "",
  "location": "",
  "summary": "",
  "experience": [
    { "title": "", "company": "", "dates": "", "bullets": [] }
  ],
  "skills": [],
  "education": [
    { "degree": "", "school": "", "dates": "", "notes": "" }
  ]
}

RESUME TEXT:
${text}`;
}

export async function importResume(filePath, { model = 'qwen2.5-coder:7b', yes = false } = {}) {
  const absPath = resolve(filePath);
  process.stderr.write(`[import] extracting text from ${absPath}\n`);
  const text = await extractText(absPath);
  if (!text.trim()) throw new Error('No text extracted from file.');

  process.stderr.write(`[import] extracted ${text.length} chars; structuring with ${model}\n`);

  const raw = await withRetry(async () => {
    const response = await ollama.chat({
      model,
      messages: [{ role: 'user', content: buildPrompt(text) }],
      stream: true,
      think: false,
      options: { num_predict: 4096, temperature: 0 },
    });
    return streamWithWatchdog(response, 'import-resume');
  }, { label: 'import-resume' });

  const cleaned = raw.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  const match = cleaned.match(/\{[\s\S]*\}/);
  if (!match) throw new Error('Model did not return JSON. Try re-running.');
  const parsed = JSON.parse(match[0]);

  console.log('\nExtracted resume preview:');
  console.log(`  Name:       ${parsed.name || '(empty)'}`);
  console.log(`  Email:      ${parsed.email || '(empty)'}`);
  console.log(`  Location:   ${parsed.location || '(empty)'}`);
  console.log(`  Summary:    ${(parsed.summary || '').slice(0, 120)}${parsed.summary?.length > 120 ? '...' : ''}`);
  console.log(`  Experience: ${parsed.experience?.length || 0} entries`);
  console.log(`  Skills:     ${parsed.skills?.length || 0} items`);
  console.log(`  Education:  ${parsed.education?.length || 0} entries`);

  let existing = false;
  try { await access(RESUME_PATH); existing = true; } catch {}

  if (!yes) {
    const msg = existing
      ? '\nWrite this to base-resume.json? (existing file will be backed up)'
      : '\nWrite this to base-resume.json?';
    if (!(await askYesNo(msg))) {
      console.log('Aborted. No file written.');
      return null;
    }
  }

  if (existing) {
    const ts = new Date().toISOString().replace(/[:.]/g, '-');
    const backup = new URL(`../base-resume.backup-${ts}.json`, import.meta.url);
    await copyFile(RESUME_PATH, backup);
    console.log(`Backup: ${backup.pathname}`);
  }

  await writeFile(RESUME_PATH, JSON.stringify(parsed, null, 2), 'utf-8');
  resetResumeCache();
  console.log(`Wrote base-resume.json`);
  return parsed;
}
