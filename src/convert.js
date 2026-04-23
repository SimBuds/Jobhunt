import { readFile, writeFile, copyFile, access } from 'node:fs/promises';
import { extname, resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Ollama } from 'ollama';
import { streamWithWatchdog, withRetry } from './_stream.js';
import { RESUME_PATH, resetResumeCache } from './tailor.js';
import { askYesNo } from './prompt.js';

const ollama = new Ollama({ host: 'http://127.0.0.1:11434' });
const PROJECT_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

const CANDIDATE_NAMES = [
  'resume.pdf', 'resume.docx', 'resume.txt',
  'Resume.pdf', 'Resume.docx', 'Resume.txt',
  'cv.pdf', 'cv.docx',
];

async function extractText(filePath) {
  const ext = extname(filePath).toLowerCase();
  try {
    if (ext === '.pdf') {
      const { PDFParse } = await import('pdf-parse');
      const buf = await readFile(filePath);
      const parser = new PDFParse({ data: new Uint8Array(buf) });
      try {
        const result = await parser.getText();
        return result.text || '';
      } finally {
        await parser.destroy().catch(() => {});
      }
    }
    if (ext === '.docx') {
      const mammoth = await import('mammoth');
      const result = await mammoth.extractRawText({ path: filePath });
      return result.value;
    }
    if (ext === '.doc') {
      throw new Error('Legacy .doc not supported. Save as .docx or export as .pdf and re-run.');
    }
    if (ext === '.txt' || ext === '.md') {
      return await readFile(filePath, 'utf-8');
    }
    throw new Error(`Unsupported file type: "${ext || '(none)'}". Use .pdf, .docx, .txt, or .md.`);
  } catch (err) {
    if (err.code === 'ENOENT') {
      throw new Error(`File not found: ${filePath}`);
    }
    if (ext === '.pdf') {
      throw new Error(`Could not read PDF: ${err.message}. Try exporting the resume as .docx or .txt and re-running.`);
    }
    if (ext === '.docx') {
      throw new Error(`Could not read DOCX: ${err.message}. Try saving as .pdf or .txt.`);
    }
    throw err;
  }
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

export async function autoDetectResumeFile() {
  for (const name of CANDIDATE_NAMES) {
    const p = join(PROJECT_ROOT, name);
    try {
      await access(p);
      return p;
    } catch {}
  }
  return null;
}

export async function convertResume(filePath, { model = 'qwen2.5-coder:7b', yes = false } = {}) {
  const absPath = resolve(filePath);
  console.log(`\nReading resume from ${absPath}`);

  const text = await extractText(absPath);
  if (!text.trim()) throw new Error(`No text could be extracted from ${absPath}. The file may be scanned images rather than text — try a different export.`);
  console.log(`Extracted ${text.length} chars of text.`);

  console.log(`Structuring with ${model} (this usually takes 30-90s)...`);
  let raw;
  try {
    raw = await withRetry(async () => {
      const response = await ollama.chat({
        model,
        messages: [{ role: 'user', content: buildPrompt(text) }],
        stream: true,
        think: false,
        options: { num_predict: 4096, temperature: 0 },
      });
      return streamWithWatchdog(response, 'convert');
    }, { label: 'convert' });
  } catch (err) {
    if (/ECONN|fetch failed|ECONNREFUSED/i.test(err.message)) {
      throw new Error(`Could not reach Ollama at 127.0.0.1:11434. Is "ollama serve" running?`);
    }
    if (/model.*not found/i.test(err.message)) {
      throw new Error(`Model "${model}" not pulled. Run: ollama pull ${model}`);
    }
    throw new Error(`LLM extraction failed: ${err.message}`);
  }

  const cleaned = raw.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  const match = cleaned.match(/\{[\s\S]*\}/);
  if (!match) throw new Error(`Model did not return JSON. First 300 chars of output: ${cleaned.slice(0, 300)}`);
  let parsed;
  try {
    parsed = JSON.parse(match[0]);
  } catch (err) {
    throw new Error(`Model returned invalid JSON: ${err.message}. Re-run the command.`);
  }

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
