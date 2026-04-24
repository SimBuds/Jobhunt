import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { loadBaseResume } from '../apply/tailor.js';
import { loadCompanies } from '../core/companies.js';
import { score, priorityFor } from './score.js';
import { loadConfig } from '../core/config.js';
import { searchLinkedIn } from './sources/linkedin.js';
import { searchJobBank } from './sources/jobbank.js';

const PIPELINE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'applications');
const PIPELINE_PATH = join(PIPELINE_DIR, 'pipeline.json');
const STALE_DAYS = 14;

export { PIPELINE_PATH };

// Toronto + ~100km radius
const LOCATION_RE = /(toronto|gta|ontario|\bon\b|canada|remote|mississauga|brampton|markham|vaughan|richmond hill|oakville|burlington|hamilton|kitchener|waterloo|cambridge|guelph|oshawa|pickering|ajax|whitby|aurora|newmarket|barrie|milton|halton|peel|durham|york region|niagara|st\.?\s*catharines|grimsby)/i;
const EXCLUDE_LOC_RE = /(united states|usa|u\.s\.a|uk\b|united kingdom|emea|apac|australia|india|brazil|germany|philippines)/i;
const ROLE_RE = /(software|frontend|front-end|front end|backend|back-end|back end|full[-\s]?stack|engineer|developer|programmer|intern|new[-\s]?grad|junior)/i;

function stripHtml(s = '') {
  return s.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

function locationOk(loc = '') {
  if (!loc) return false;
  if (EXCLUDE_LOC_RE.test(loc) && !/canada|toronto|remote/i.test(loc)) return false;
  return LOCATION_RE.test(loc);
}

function fuzzyKey(company, role) {
  const cleanRole = (role || '')
    .toLowerCase()
    .replace(/[\s\-–—,()\/]+/g, ' ')
    .replace(/\b(remote|toronto|canada|usa?|hybrid|onsite|on[-\s]?site|gta|ontario|hq)\b/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return `${(company || '').toLowerCase().trim()}::${cleanRole}`;
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

async function fetchGreenhouse(slug, verbose) {
  const url = `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs?content=true`;
  try {
    const data = await fetchJson(url);
    return {
      ok: true,
      slug,
      jobs: (data.jobs || []).map(j => ({
        company: slug,
        role: j.title,
        url: j.absolute_url,
        location: j.location?.name || '',
        salary: '',
        tech_stack: [],
        ats_platform: 'greenhouse',
        description: stripHtml(j.content || ''),
      })),
    };
  } catch (err) {
    if (verbose) process.stderr.write(`[scan] greenhouse/${slug}: ${err.message}\n`);
    return { ok: false, slug, jobs: [] };
  }
}

async function fetchLever(slug, verbose) {
  const url = `https://api.lever.co/v0/postings/${slug}?mode=json`;
  try {
    const data = await fetchJson(url);
    return {
      ok: true,
      slug,
      jobs: (Array.isArray(data) ? data : []).map(j => ({
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
      })),
    };
  } catch (err) {
    if (verbose) process.stderr.write(`[scan] lever/${slug}: ${err.message}\n`);
    return { ok: false, slug, jobs: [] };
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

export async function scan({ sources = ['api'], seniorityOverride = null } = {}) {
  const config = await loadConfig();
  const verbose = !!config.verbose_scan;
  const seniorityPolicy = seniorityOverride || config.seniority_policy;
  const seniorCap = config.senior_score_cap;
  const pipelineCap = config.max_pipeline_size;

  const companies = await loadCompanies();
  const wantAll = sources.includes('all');
  const wantApi = sources.includes('api') || wantAll;
  const useLinkedIn = sources.includes('linkedin') || wantAll;
  const useJobBank  = sources.includes('jobbank')  || wantAll;
  const anyScraper  = useLinkedIn || useJobBank;

  if (wantApi && !anyScraper && !companies.greenhouse.length && !companies.lever.length) {
    console.log('No companies configured. Edit data/companies.json or pass --sources all.');
    return { jobs: [], added: 0 };
  }

  if (useLinkedIn) {
    console.log('\n⚠  LinkedIn scraping is best-effort and may violate ToS.');
    console.log('   A browser will open; rate limits + CAPTCHAs are possible.\n');
  }
  if (useJobBank) {
    console.log('Scanning Job Bank (public board).\n');
  }

  const resume = await loadBaseResume();
  const existing = await loadExistingPipeline();
  const existingByUrl = new Map(existing.map(j => [j.url, j]));

  const greenhouseResults = wantApi
    ? await Promise.all(companies.greenhouse.map(s => fetchGreenhouse(s, verbose)))
    : [];
  const leverResults = wantApi
    ? await Promise.all(companies.lever.map(s => fetchLever(s, verbose)))
    : [];

  const ghOk = greenhouseResults.filter(r => r.ok).length;
  const leverOk = leverResults.filter(r => r.ok).length;
  const ghSkipped = greenhouseResults.length - ghOk;
  const leverSkipped = leverResults.length - leverOk;

  const apiResults = [
    ...greenhouseResults.flatMap(r => r.jobs),
    ...leverResults.flatMap(r => r.jobs),
  ];

  async function safeRun(label, fn) {
    try { return await fn(); } catch (err) {
      process.stderr.write(`[${label}] aborted: ${err.message}\n`);
      return [];
    }
  }

  const linkedInResults = useLinkedIn ? await safeRun('linkedin', () => searchLinkedIn(companies.linkedin_queries)) : [];
  const jobBankResults  = useJobBank  ? await safeRun('jobbank',  () => searchJobBank(companies.jobbank_queries)) : [];

  const results = [
    ...apiResults,
    ...linkedInResults,
    ...jobBankResults,
  ];

  if (wantApi) {
    console.log(
      `Fetched ${ghOk} Greenhouse + ${leverOk} Lever boards` +
      (ghSkipped + leverSkipped ? ` (${ghSkipped + leverSkipped} skipped)` : '')
    );
  }
  const scraperCounts = [];
  if (useLinkedIn) scraperCounts.push(`LinkedIn: ${linkedInResults.length}`);
  if (useJobBank)  scraperCounts.push(`JobBank: ${jobBankResults.length}`);
  if (scraperCounts.length) console.log(scraperCounts.join(' | '));

  const today = new Date().toISOString().slice(0, 10);
  const seenUrls = new Set();
  const seenFuzzy = new Map(); // fuzzyKey -> index into `kept`
  const kept = [];
  let droppedLocation = 0;
  let droppedRole = 0;
  let droppedSenior = 0;
  let droppedDup = 0;

  for (const raw of results) {
    if (!raw.url || seenUrls.has(raw.url)) { droppedDup++; continue; }
    seenUrls.add(raw.url);
    if (!ROLE_RE.test(raw.role || '')) { droppedRole++; continue; }
    if (!locationOk(raw.location)) { droppedLocation++; continue; }

    const scored = score(
      { title: raw.role, description: raw.description },
      resume,
      { seniorityPolicy, seniorCap },
    );
    if (scored === null) { droppedSenior++; continue; }

    const prior = existingByUrl.get(raw.url);
    const entry = {
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
    };

    const fk = fuzzyKey(entry.company, entry.role);
    const dupIdx = seenFuzzy.get(fk);
    if (dupIdx !== undefined) {
      const existing = kept[dupIdx];
      if (entry.fit_score > existing.fit_score) kept[dupIdx] = entry;
      droppedDup++;
      continue;
    }
    seenFuzzy.set(fk, kept.length);
    kept.push(entry);
  }

  // Preserve prior entries that didn't appear in this scan (re-inserted without
  // fuzzy-dedup, since they're known-good historic state).
  const keptUrls = new Set(kept.map(j => j.url));
  for (const prior of existing) {
    if (keptUrls.has(prior.url)) continue;
    const lastSeen = new Date(prior.last_seen || prior.date_discovered || today).getTime();
    const ageDays = (Date.now() - lastSeen) / 86_400_000;
    if (ageDays > STALE_DAYS && !prior.applied) continue;
    kept.push({ ...prior, status: prior.applied ? prior.status : 'stale' });
  }

  kept.sort((a, b) => b.fit_score - a.fit_score);

  let trimmed = 0;
  let final = kept;
  if (final.length > pipelineCap) {
    trimmed = final.length - pipelineCap;
    final = final.slice(0, pipelineCap);
  }

  await mkdir(PIPELINE_DIR, { recursive: true });
  await writeFile(PIPELINE_PATH, JSON.stringify(final, null, 2), 'utf-8');

  const newCount = final.filter(j => !existingByUrl.has(j.url)).length;
  const dropParts = [];
  if (droppedSenior)   dropParts.push(`${droppedSenior} senior`);
  if (droppedDup)      dropParts.push(`${droppedDup} duplicates`);
  if (droppedLocation) dropParts.push(`${droppedLocation} out-of-region`);
  if (droppedRole)     dropParts.push(`${droppedRole} off-topic`);
  if (trimmed)         dropParts.push(`${trimmed} beyond cap`);

  console.log(
    `\nFiltered ${results.length} postings -> ${final.length} in pipeline ` +
    `(${newCount} new${dropParts.length ? `, dropped ${dropParts.join(', ')}` : ''})\n`
  );
  for (const j of final.slice(0, 15)) {
    const flag = j.applied ? '[applied]' : j.status === 'stale' ? '[stale]  ' : '         ';
    console.log(`${String(j.fit_score).padStart(3)}% fit ${flag} ${j.company} — ${j.role}`);
  }
  if (final.length > 15) console.log(`... and ${final.length - 15} more in applications/pipeline.json`);

  return { jobs: final, added: newCount };
}

export async function readPipeline() {
  return loadExistingPipeline();
}

export async function writePipeline(jobs) {
  await mkdir(PIPELINE_DIR, { recursive: true });
  await writeFile(PIPELINE_PATH, JSON.stringify(jobs, null, 2), 'utf-8');
}
