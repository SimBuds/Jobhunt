import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { loadBaseResume } from '../apply/tailor.js';
import { loadCompanies } from '../core/companies.js';
import { score, priorityFor } from './score.js';
import { loadConfig } from '../core/config.js';
import { REGISTRY, expandSources, isScraperSource } from './sources/index.js';

const PIPELINE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'applications');
const PIPELINE_PATH = join(PIPELINE_DIR, 'pipeline.json');
const STALE_DAYS = 14;

export { PIPELINE_PATH };

// Toronto + ~100km radius
const LOCATION_RE = /(toronto|gta|ontario|\bon\b|canada|remote|mississauga|brampton|markham|vaughan|richmond hill|oakville|burlington|hamilton|kitchener|waterloo|cambridge|guelph|oshawa|pickering|ajax|whitby|aurora|newmarket|barrie|milton|halton|peel|durham|york region|niagara|st\.?\s*catharines|grimsby)/i;
const EXCLUDE_LOC_RE = /(united states|usa|u\.s\.a|uk\b|united kingdom|emea|apac|australia|india|brazil|germany|philippines)/i;
const ROLE_RE = /(software|frontend|front-end|front end|backend|back-end|back end|full[-\s]?stack|engineer|developer|programmer|intern|new[-\s]?grad|junior)/i;
const ROLE_DENY_BASE = [
  'hris', 'salesforce', '\\bsap\\b', 'workday admin',
  'data engineer', 'ml engineer', 'machine learning engineer',
  '\\bdevops\\b', '\\bsre\\b', 'site reliability', 'platform engineer',
  'security engineer', 'cloud engineer', 'infrastructure engineer',
  'graphic designer', 'ux designer', 'ui designer', 'product designer',
  'test engineer', 'qa engineer', '\\bsdet\\b', 'quality assurance',
  'technical writer', 'scrum master', 'product manager', 'project manager',
  'technical program manager', 'engineering manager', 'data scientist',
  'data analyst', 'business analyst', 'solutions architect',
  'sales engineer', 'solutions engineer', 'support engineer',
  'technical support engineer', 'field engineer', 'field sales',
  'customer engineer', 'customer success engineer',
  'forward deployed', 'application engineer', 'implementation engineer',
  'supply chain', 'embedded software', 'embedded systems',
  'database administrator', '\\bdba\\b',
  'network engineer', 'systems engineer',
  'automation designer', 'automation engineer',
  'solutions consultant', 'technical consultant',
];

function buildDenyRe(extras = []) {
  const all = [...ROLE_DENY_BASE, ...extras.map(e => e.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))];
  return new RegExp(`\\b(${all.join('|')})\\b`, 'i');
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
  const roleDenyRe = buildDenyRe(Array.isArray(config.role_deny_extras) ? config.role_deny_extras : []);

  const companies = await loadCompanies();
  const activeSources = expandSources(sources);

  if (activeSources.includes('linkedin')) {
    console.log('\n⚠  LinkedIn scraping is best-effort and may violate ToS.');
    console.log('   A browser will open; rate limits + CAPTCHAs are possible.\n');
  }
  if (activeSources.includes('jobbank')) {
    console.log('Scanning Job Bank (public board).\n');
  }

  const resume = await loadBaseResume();
  const existing = await loadExistingPipeline();
  const existingByUrl = new Map(existing.map(j => [j.url, j]));

  // Run API sources in parallel; scraper sources sequentially (each holds a browser).
  const apiNames = activeSources.filter(n => !isScraperSource(n));
  const scraperNames = activeSources.filter(n => isScraperSource(n));

  async function safeRun(name) {
    try {
      return await REGISTRY[name].fetchAll({ companies, verbose });
    } catch (err) {
      process.stderr.write(`[${name}] aborted: ${err.message}\n`);
      return { results: [], summary: `${name}: aborted (${err.message})` };
    }
  }

  const apiOutputs = await Promise.all(apiNames.map(safeRun));
  const scraperOutputs = [];
  for (const n of scraperNames) scraperOutputs.push(await safeRun(n));

  const sourceOutputs = [...apiOutputs, ...scraperOutputs];
  const results = sourceOutputs.flatMap(o => o.results);

  const summaries = sourceOutputs.map(o => o.summary).filter(Boolean);
  if (summaries.length) console.log(summaries.join(' | '));

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
    if (roleDenyRe.test(raw.role || '')) { droppedRole++; continue; }
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

  // Preserve prior entries that didn't appear in this scan. Re-apply role filters
  // so entries added before the deny-list expanded don't persist forever.
  const keptUrls = new Set(kept.map(j => j.url));
  for (const prior of existing) {
    if (keptUrls.has(prior.url)) continue;
    if (prior.applied) {
      // Always keep applied entries — they're history.
      kept.push({ ...prior });
      continue;
    }
    if (!ROLE_RE.test(prior.role || '')) continue;
    if (roleDenyRe.test(prior.role || '')) continue;
    const lastSeen = new Date(prior.last_seen || prior.date_discovered || today).getTime();
    const ageDays = (Date.now() - lastSeen) / 86_400_000;
    if (ageDays > STALE_DAYS) continue;
    kept.push({ ...prior, status: 'stale' });
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
