import { load } from 'cheerio';
import { launchStealthBrowser, jitter } from './browser-search.js';

const MAX_QUERIES = 3;
const MAX_PER_QUERY = 25;

function buildUrl(query, start = 0) {
  // distance is miles on LinkedIn; 62 miles ≈ 100km
  // f_JT=F,P,C,T,I,V,O covers full-time, part-time, contract, temp, intern, volunteer, other
  // f_WT=1,2,3 covers on-site, remote, hybrid
  const params = new URLSearchParams({
    keywords: query,
    location: 'Toronto, Ontario, Canada',
    distance: '62',
    f_TPR: 'r1209600',
    f_JT: 'F,P,C,T,I',
    f_WT: '1,2,3',
    start: String(start),
  });
  return `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?${params}`;
}

function parseFragment(html) {
  const $ = load(html);
  const jobs = [];
  $('li').each((_, li) => {
    const $li = $(li);
    const url = $li.find('a.base-card__full-link').attr('href') || $li.find('a[data-tracking-control-name]').attr('href');
    const title = $li.find('.base-search-card__title').text().trim();
    const company = $li.find('.base-search-card__subtitle').text().trim();
    const location = $li.find('.job-search-card__location').text().trim();
    if (!url || !title) return;
    const cleanUrl = url.split('?')[0];
    jobs.push({
      company,
      role: title,
      url: cleanUrl,
      location,
      salary: '',
      tech_stack: [],
      ats_platform: 'linkedin',
      description: `${title} at ${company}. ${location}.`,
    });
  });
  return jobs;
}

export async function searchLinkedIn(queries) {
  const out = [];
  const clipped = queries.slice(0, MAX_QUERIES);
  if (!clipped.length) return out;

  const { browser, context } = await launchStealthBrowser({ headless: false });
  try {
    const page = await context.newPage();
    for (const q of clipped) {
      process.stderr.write(`[linkedin] searching "${q}"\n`);
      try {
        const resp = await page.goto(buildUrl(q, 0), { waitUntil: 'domcontentloaded', timeout: 20_000 });
        const status = resp?.status() ?? 0;
        if (status >= 400) {
          process.stderr.write(`[linkedin] HTTP ${status} for "${q}" — likely rate-limited; stopping.\n`);
          break;
        }
        const html = await page.content();
        const results = parseFragment(html).slice(0, MAX_PER_QUERY);
        process.stderr.write(`[linkedin] "${q}" -> ${results.length} jobs\n`);
        out.push(...results);
      } catch (err) {
        process.stderr.write(`[linkedin] "${q}" failed: ${err.message}\n`);
      }
      await jitter(4000, 8000);
    }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
  return out;
}
