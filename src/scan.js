import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { loadBaseResume } from './tailor.js';
import { loadCompanies } from './companies.js';
import { score, priorityFor } from './score.js';

const PIPELINE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', 'applications');
const PIPELINE_PATH = join(PIPELINE_DIR, 'pipeline.json');
const STALE_DAYS = 14;

export { PIPELINE_PATH };

const LOCATION_RE = /(toronto|gta|ontario|\bon\b|canada|remote)/i;
const EXCLUDE_LOC_RE = /(united states|usa|u\.s\.a|uk\b|united kingdom|emea|apac|australia|india|brazil|germany)/i;
const ROLE_RE = /(software|frontend|front-end|front end|backend|back-end|back end|full[-\s]?stack|engineer|developer|programmer|intern|new[-\s]?grad|junior)/i;

function stripHtml(s = '') {
  return s.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

function locationOk(loc = '') {
  if (!loc) return false;
  if (EXCLUDE_LOC_RE.test(loc) && !/canada|toronto|remote/i.test(loc)) return false;
  return LOCATION_RE.test(loc);
}

async function fetchJson(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15_000);
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { 'User-Agent': 'job-agent/1.0' } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

async function fetchGreenhouse(slug) {
  const url = `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs?content=true`;
  try {
    const data = await fetchJson(url);
    return (data.jobs || []).map(j => ({
      company: slug,
      role: j.title,
      url: j.absolute_url,
      location: j.location?.name || '',
      salary: '',
      tech_stack: [],
      ats_platform: 'greenhouse',
      description: stripHtml(j.content || ''),
    }));
  } catch (err) {
    process.stderr.write(`[scan] greenhouse/${slug}: ${err.message}\n`);
    return [];
  }
}

async function fetchLever(slug) {
  const url = `https://api.lever.co/v0/postings/${slug}?mode=json`;
  try {
    const data = await fetchJson(url);
    return (Array.isArray(data) ? data : []).map(j => ({
      company: slug,
      role: j.text,
      url: j.hostedUrl,
      location: j.categories?.location || '',
      salary: '',
      tech_stack: (j.categories?.team ? [j.categories.team] : []),
      ats_platform: 'lever',
      description: stripHtml(
        (j.descriptionPlain || j.description || '') +
        ' ' +
        (j.lists || []).map(l => l.text + ' ' + stripHtml(l.content || '')).join(' ')
      ),
    }));
  } catch (err) {
    process.stderr.write(`[scan] lever/${slug}: ${err.message}\n`);
    return [];
  }
}

async function loadExistingPipeline() {
  try {
    const raw = await readFile(PIPELINE_PATH, 'utf-8');
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

export async function scan() {
  const companies = await loadCompanies();
  if (!companies.greenhouse.length && !companies.lever.length) {
    console.log('No companies configured. Edit data/companies.json to add Greenhouse/Lever slugs.');
    return { jobs: [], added: 0 };
  }

  const resume = await loadBaseResume();
  const existing = await loadExistingPipeline();
  const existingByUrl = new Map(existing.map(j => [j.url, j]));

  const fetchTasks = [
    ...companies.greenhouse.map(s => fetchGreenhouse(s)),
    ...companies.lever.map(s => fetchLever(s)),
  ];
  const results = (await Promise.all(fetchTasks)).flat();

  const today = new Date().toISOString().slice(0, 10);
  const seenUrls = new Set();
  const kept = [];

  for (const raw of results) {
    if (!raw.url || seenUrls.has(raw.url)) continue;
    seenUrls.add(raw.url);
    if (!ROLE_RE.test(raw.role || '')) continue;
    if (!locationOk(raw.location)) continue;

    const prior = existingByUrl.get(raw.url);
    const scored = score({ title: raw.role, description: raw.description }, resume);

    kept.push({
      company: raw.company,
      role: raw.role,
      url: raw.url,
      location: raw.location,
      salary: raw.salary,
      tech_stack: raw.tech_stack,
      ats_platform: raw.ats_platform,
      date_discovered: prior?.date_discovered || today,
      last_seen: today,
      fit_score: scored.score,
      priority: priorityFor(scored.score),
      applied: prior?.applied || false,
      status: prior?.applied ? (prior.status || 'submitted') : 'new',
      notes: scored.missing_keywords.length
        ? `missing: ${scored.missing_keywords.join(', ')} | ${scored.rationale}`
        : scored.rationale,
    });
  }

  const keptUrls = new Set(kept.map(j => j.url));
  for (const prior of existing) {
    if (keptUrls.has(prior.url)) continue;
    const lastSeen = new Date(prior.last_seen || prior.date_discovered || today).getTime();
    const ageDays = (Date.now() - lastSeen) / 86_400_000;
    if (ageDays > STALE_DAYS && !prior.applied) continue;
    kept.push({ ...prior, status: prior.applied ? prior.status : 'stale' });
  }

  kept.sort((a, b) => b.fit_score - a.fit_score);

  await mkdir(PIPELINE_DIR, { recursive: true });
  await writeFile(PIPELINE_PATH, JSON.stringify(kept, null, 2), 'utf-8');

  const newCount = kept.filter(j => !existingByUrl.has(j.url)).length;
  console.log(`\nScanned ${results.length} postings -> ${kept.length} in pipeline (${newCount} new)\n`);
  for (const j of kept.slice(0, 15)) {
    const flag = j.applied ? '[applied]' : j.status === 'stale' ? '[stale]  ' : '         ';
    console.log(`${String(j.fit_score).padStart(3)}% fit ${flag} ${j.company} — ${j.role}`);
  }
  if (kept.length > 15) console.log(`... and ${kept.length - 15} more in applications/pipeline.json`);

  return { jobs: kept, added: newCount };
}

export async function readPipeline() {
  return loadExistingPipeline();
}

export async function writePipeline(jobs) {
  await mkdir(PIPELINE_DIR, { recursive: true });
  await writeFile(PIPELINE_PATH, JSON.stringify(jobs, null, 2), 'utf-8');
}
