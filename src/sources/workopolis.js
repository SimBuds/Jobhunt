import { launchStealthBrowser, jitter } from './browser-search.js';

// Note: Workopolis was acquired/wound down in 2018. The site may return zero
// results or redirect; we attempt a best-effort parse and warn if empty.

const MAX_QUERIES = 3;
const MAX_PER_QUERY = 25;

function buildUrl(query) {
  const params = new URLSearchParams({
    q: query,
    l: 'Toronto, ON',
  });
  return `https://www.workopolis.com/jobsearch/find-jobs?${params}`;
}

export async function searchWorkopolis(queries) {
  const out = [];
  const clipped = queries.slice(0, MAX_QUERIES);
  if (!clipped.length) return out;

  const { browser, context } = await launchStealthBrowser({ headless: false });
  try {
    const page = await context.newPage();
    let warnedDeprecated = false;
    for (const q of clipped) {
      process.stderr.write(`[workopolis] searching "${q}"\n`);
      try {
        await page.goto(buildUrl(q), { waitUntil: 'domcontentloaded', timeout: 25_000 });
        await page.waitForSelector('.JobInfo, article, .job-card, [data-testid*="job"]', { timeout: 8_000 }).catch(() => {});

        const cards = await page.$$eval(
          '.JobInfo, article.job, .job-card, [data-testid*="job-card"]',
          nodes => nodes.map(n => {
            const linkEl = n.querySelector('a[href*="/job"], h2 a, h3 a');
            const href = linkEl?.getAttribute('href') || '';
            const title = (n.querySelector('.JobTitle, h2, h3')?.textContent || linkEl?.textContent || '').trim();
            const company = n.querySelector('.CompanyName, .company')?.textContent?.trim() || '';
            const location = n.querySelector('.Location, .location')?.textContent?.trim() || '';
            const snippet = n.querySelector('.JobSummary, .summary, p')?.textContent?.trim() || '';
            return { href, title, company, location, snippet };
          })
        ).catch(() => []);

        const results = cards
          .filter(c => c.href && c.title)
          .slice(0, MAX_PER_QUERY)
          .map(c => ({
            company: c.company || 'Workopolis',
            role: c.title,
            url: c.href.startsWith('http') ? c.href : `https://www.workopolis.com${c.href}`,
            location: c.location,
            salary: '',
            tech_stack: [],
            ats_platform: 'workopolis',
            description: `${c.title} at ${c.company}. ${c.location}. ${c.snippet}`,
          }));

        if (!results.length && !warnedDeprecated) {
          process.stderr.write('[workopolis] no results — the site was discontinued in 2018 and may be dormant. Consider dropping this source.\n');
          warnedDeprecated = true;
        }
        process.stderr.write(`[workopolis] "${q}" -> ${results.length} jobs\n`);
        out.push(...results);
      } catch (err) {
        process.stderr.write(`[workopolis] "${q}" failed: ${err.message}\n`);
      }
      await jitter(3000, 6000);
    }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
  return out;
}
