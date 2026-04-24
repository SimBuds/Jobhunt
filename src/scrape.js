import * as cheerio from 'cheerio';
import { readFile, writeFile, mkdir } from 'fs/promises';
import { createHash } from 'crypto';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const CACHE_DIR = join(__dirname, '..', 'data', 'scrape-cache');
const CACHE_TTL_MS = 24 * 60 * 60 * 1000; // 24h

const USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

function extractLinkedIn($) {
  const selectors = [
    '.description__text',
    '.show-more-less-html__markup',
    '[class*="description"]',
    '.core-section-container__content',
  ];
  for (const sel of selectors) {
    const text = $(sel).text().trim();
    if (text.length > 100) return text;
  }
  return null;
}

function extractLever($) {
  const parts = [];
  const content = $('.section-wrapper.page-full-width, .content, [class*="posting"]');
  content.find('.section, .content-wrapper > div').each((_, el) => {
    const text = $(el).text().trim();
    if (text.length > 20) parts.push(text);
  });
  if (parts.length) return parts.join('\n\n');
  const main = content.text().trim();
  return main.length > 100 ? main : null;
}

function extractGreenhouse($) {
  const selectors = ['#content', '.job__description', '#app_body', '.body'];
  for (const sel of selectors) {
    const text = $(sel).text().trim();
    if (text.length > 100) return text;
  }
  return null;
}

function extractWorkday($) {
  const selectors = [
    '[data-automation-id="jobPostingDescription"]',
    '.job-description',
    '[class*="jobDescription"]',
  ];
  for (const sel of selectors) {
    const text = $(sel).text().trim();
    if (text.length > 100) return text;
  }
  return null;
}

function extractGeneric($) {
  // Remove nav, header, footer, sidebar noise
  $('nav, header, footer, aside, script, style, [role="navigation"], [role="banner"]').remove();

  // Try common job description selectors
  const selectors = [
    '[class*="job-description"]',
    '[class*="jobDescription"]',
    '[class*="job_description"]',
    '[id*="job-description"]',
    'article',
    'main',
    '.content',
    '#content',
  ];
  for (const sel of selectors) {
    const text = $(sel).text().trim();
    if (text.length > 200) return text;
  }

  // Fallback: largest text block in body
  return $('body').text().trim();
}

function extractAshby($) {
  const selectors = ['.ashby-job-posting-right-pane', '[class*="_description"]', '._descriptionText_', 'main'];
  for (const sel of selectors) {
    const text = $(sel).text().trim();
    if (text.length > 100) return text;
  }
  return null;
}

function extractSmartRecruiters($) {
  const selectors = ['#st-jobDescription', '.job-sections', '[class*="job-description"]', 'main'];
  for (const sel of selectors) {
    const text = $(sel).text().trim();
    if (text.length > 100) return text;
  }
  return null;
}

function extractICIMS($) {
  const selectors = ['.iCIMS_JobContent', '#iCIMS_Content_Iframe', '.iCIMS_InfoMsg', 'main'];
  for (const sel of selectors) {
    const text = $(sel).text().trim();
    if (text.length > 100) return text;
  }
  return null;
}

export function detectPlatform(url) {
  if (url.includes('linkedin.com')) return 'linkedin';
  if (url.includes('lever.co')) return 'lever';
  if (url.includes('greenhouse.io') || url.includes('boards.greenhouse') || url.includes('job-boards.greenhouse')) return 'greenhouse';
  if (url.includes('myworkdayjobs.com') || url.includes('workday.com') || url.includes('wd1.myworkdaysite') || url.includes('wd3.myworkdaysite') || url.includes('wd5.myworkdaysite')) return 'workday';
  if (url.includes('ashbyhq.com') || url.includes('jobs.ashbyhq')) return 'ashby';
  if (url.includes('smartrecruiters.com')) return 'smartrecruiters';
  if (url.includes('icims.com')) return 'icims';
  if (url.includes('scotiabank.com') || url.includes('jobs.rbc.com') || url.includes('td.com/careers') || url.includes('bmo.com/careers') || url.includes('cibc.com/careers')) return 'workday';
  return 'generic';
}

function cleanText(text) {
  return text
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/^\s+|\s+$/gm, '')
    .trim();
}

async function readCache(url) {
  try {
    const key = createHash('sha1').update(url).digest('hex');
    const path = join(CACHE_DIR, `${key}.json`);
    const raw = await readFile(path, 'utf-8');
    const entry = JSON.parse(raw);
    if (Date.now() - entry.ts > CACHE_TTL_MS) return null;
    return entry.data;
  } catch {
    return null;
  }
}

async function writeCache(url, data) {
  try {
    await mkdir(CACHE_DIR, { recursive: true });
    const key = createHash('sha1').update(url).digest('hex');
    const path = join(CACHE_DIR, `${key}.json`);
    await writeFile(path, JSON.stringify({ ts: Date.now(), data }));
  } catch { /* cache write failures are non-fatal */ }
}

export async function scrape(url, { timeoutMs = 20_000, useCache = true } = {}) {
  if (useCache) {
    const cached = await readCache(url);
    if (cached) {
      process.stderr.write(`[scrape] cache hit\n`);
      return cached;
    }
  }
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(url, {
      headers: { 'User-Agent': USER_AGENT },
      redirect: 'follow',
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error(`Fetch timed out after ${timeoutMs}ms: ${url}`);
    }
    throw err;
  } finally {
    clearTimeout(t);
  }
  if (!res.ok) throw new Error(`Failed to fetch ${url}: ${res.status}`);

  const html = await res.text();
  const $ = cheerio.load(html);
  const platform = detectPlatform(url);

  const extractors = {
    linkedin: extractLinkedIn,
    lever: extractLever,
    greenhouse: extractGreenhouse,
    workday: extractWorkday,
    ashby: extractAshby,
    smartrecruiters: extractSmartRecruiters,
    icims: extractICIMS,
    generic: extractGeneric,
  };
  const raw = extractors[platform]($) || extractGeneric($);

  if (!raw || raw.length < 50) {
    throw new Error('Could not extract meaningful job description from the page.');
  }

  return { platform, description: cleanText(raw), url };
}
