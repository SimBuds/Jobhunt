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

async function extractFromPdf(filePath) {
  const { PDFParse } = await import('pdf-parse');
  const buf = await readFile(filePath);
  const parser = new PDFParse({ data: new Uint8Array(buf) });
  try {
    const textResult = await parser.getText();
    let links = [];
    try {
      const info = await parser.getInfo({ parsePageInfo: true });
      for (const page of info.pages || []) {
        for (const l of page.links || []) {
          if (l?.url) links.push({ url: l.url, text: (l.text || '').trim() });
        }
      }
    } catch (err) {
      process.stderr.write(`[convert] hyperlink extraction failed: ${err.message}\n`);
    }
    return { text: textResult.text || '', links };
  } finally {
    await parser.destroy().catch(() => {});
  }
}

async function extractFromDocx(filePath) {
  const mammoth = await import('mammoth');
  const { load } = await import('cheerio');
  const [raw, html] = await Promise.all([
    mammoth.extractRawText({ path: filePath }),
    mammoth.convertToHtml({ path: filePath }).catch(() => ({ value: '' })),
  ]);
  const links = [];
  if (html.value) {
    const $ = load(html.value);
    $('a[href]').each((_, a) => {
      const url = $(a).attr('href');
      const text = $(a).text().trim();
      if (url && /^https?:|^mailto:/i.test(url)) links.push({ url, text });
    });
  }
  return { text: raw.value || '', links };
}

async function extractText(filePath) {
  const ext = extname(filePath).toLowerCase();
  try {
    if (ext === '.pdf') return await extractFromPdf(filePath);
    if (ext === '.docx') return await extractFromDocx(filePath);
    if (ext === '.doc') throw new Error('Legacy .doc not supported. Save as .docx or export as .pdf and re-run.');
    if (ext === '.txt' || ext === '.md') return { text: await readFile(filePath, 'utf-8'), links: [] };
    throw new Error(`Unsupported file type: "${ext || '(none)'}". Use .pdf, .docx, .txt, or .md.`);
  } catch (err) {
    if (err.code === 'ENOENT') throw new Error(`File not found: ${filePath}`);
    if (ext === '.pdf') throw new Error(`Could not read PDF: ${err.message}. Try exporting the resume as .docx or .txt and re-running.`);
    if (ext === '.docx') throw new Error(`Could not read DOCX: ${err.message}. Try saving as .pdf or .txt.`);
    throw err;
  }
}

function dedupeLinks(links) {
  const seen = new Set();
  const out = [];
  for (const l of links) {
    const key = l.url.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(l);
  }
  return out;
}

function buildPrompt(text, links) {
  const linkBlock = links.length
    ? `\nDETECTED HYPERLINKS (extracted from the original file — use these exact URLs for the linkedin / github / website fields when the anchor text matches):\n` +
      links.map(l => `- "${l.text}" -> ${l.url}`).join('\n') + '\n'
    : '';
  return `You convert a resume into strict JSON. Use ONLY information present in the source. If a field is missing, use an empty string or empty array. Never invent data.

The RESUME TEXT below is plain text extracted from the file. Anchor text like "LinkedIn", "GitHub", or "Website" will appear as plain words — the actual URLs are listed separately under DETECTED HYPERLINKS. Map them into the right field:
- a linkedin.com URL -> "linkedin"
- a github.com URL -> "github"
- any other personal site URL -> "website"
- mailto: URLs -> "email" (strip the prefix)

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
${linkBlock}
RESUME TEXT:
${text}`;
}

// The LLM frequently fills URL fields with the anchor text ("LinkedIn") or a
// bare domain ("caseyhsu.com") rather than the real URL that lives in the
// file's link annotations. Backfill overrides any value that doesn't look like
// a usable URL/email.
const isUrl = v => typeof v === 'string' && /^https?:\/\//i.test(v.trim());
const isEmail = v => typeof v === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

function backfillLinks(parsed, links) {
  const pick = (host) =>
    links.find(l => {
      try { return new URL(l.url).hostname.toLowerCase().includes(host); }
      catch { return false; }
    })?.url || '';

  const linkedin = pick('linkedin.com');
  const github = pick('github.com');
  if (linkedin && !isUrl(parsed.linkedin)) parsed.linkedin = linkedin;
  if (github && !isUrl(parsed.github)) parsed.github = github;

  if (!isEmail(parsed.email)) {
    const mail = links.find(l => l.url.toLowerCase().startsWith('mailto:'))?.url;
    if (mail) parsed.email = mail.replace(/^mailto:/i, '').split('?')[0];
  }

  if (!isUrl(parsed.website)) {
    const excluded = /linkedin\.com|github\.com|twitter\.com|x\.com|facebook\.com|instagram\.com/i;
    const site = links.find(l => /^https?:/i.test(l.url) && !excluded.test(l.url))?.url;
    if (site) parsed.website = site;
  }
  return parsed;
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

  const { text, links: rawLinks } = await extractText(absPath);
  if (!text.trim()) throw new Error(`No text could be extracted from ${absPath}. The file may be scanned images rather than text — try a different export.`);
  const links = dedupeLinks(rawLinks);
  console.log(`Extracted ${text.length} chars of text and ${links.length} hyperlinks.`);
  if (links.length) {
    for (const l of links) console.log(`  link: "${l.text}" -> ${l.url}`);
  }

  console.log(`Structuring with ${model} (this usually takes 30-90s)...`);
  let raw;
  try {
    raw = await withRetry(async () => {
      const response = await ollama.chat({
        model,
        messages: [{ role: 'user', content: buildPrompt(text, links) }],
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

  backfillLinks(parsed, links);

  console.log('\nExtracted resume preview:');
  console.log(`  LinkedIn:   ${parsed.linkedin || '(empty)'}`);
  console.log(`  GitHub:     ${parsed.github || '(empty)'}`);
  console.log(`  Website:    ${parsed.website || '(empty)'}`);
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
