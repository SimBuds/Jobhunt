import { launchStealthBrowser, jitter } from './browser-search.js';

const MAX_QUERIES = 3;
const MAX_PER_QUERY = 25;

function buildUrl(query) {
  const params = new URLSearchParams({
    searchstring: query,
    locationstring: 'Toronto, ON',
    sort: 'M',
  });
  return `https://www.jobbank.gc.ca/jobsearch/jobsearch?${params}`;
}

export async function searchJobBank(queries) {
  const out = [];
  const clipped = queries.slice(0, MAX_QUERIES);
  if (!clipped.length) return out;

  const { browser, context } = await launchStealthBrowser({ headless: false });
  try {
    const page = await context.newPage();
    for (const q of clipped) {
      process.stderr.write(`[jobbank] searching "${q}"\n`);
      try {
        await page.goto(buildUrl(q), { waitUntil: 'domcontentloaded', timeout: 25_000 });
        await page.waitForSelector('article, .results-jobs, #ajaxupdateform', { timeout: 10_000 }).catch(() => {});

        const cards = await page.$$eval('article', nodes =>
          nodes.map(n => {
            const linkEl = n.querySelector('a.resultJobItem, a[href*="jobposting"]');
            const href = linkEl?.getAttribute('href') || '';
            const title = n.querySelector('.noctitle, h3, .title')?.textContent?.trim() || '';
            const company = n.querySelector('.business')?.textContent?.trim() || '';
            const location = n.querySelector('.location')?.textContent?.trim() || '';
            const salary = n.querySelector('.salary')?.textContent?.trim() || '';
            const snippet = n.querySelector('.summary, p')?.textContent?.trim() || '';
            return { href, title, company, location, salary, snippet };
          })
        ).catch(() => []);

        const results = cards
          .filter(c => c.href && c.title)
          .slice(0, MAX_PER_QUERY)
          .map(c => ({
            company: c.company,
            role: c.title,
            url: c.href.startsWith('http') ? c.href : `https://www.jobbank.gc.ca${c.href}`,
            location: c.location,
            salary: c.salary,
            tech_stack: [],
            ats_platform: 'jobbank',
            description: `${c.title} at ${c.company}. ${c.location}. ${c.snippet}`,
          }));

        process.stderr.write(`[jobbank] "${q}" -> ${results.length} jobs\n`);
        out.push(...results);
      } catch (err) {
        process.stderr.write(`[jobbank] "${q}" failed: ${err.message}\n`);
      }
      await jitter(3000, 6000);
    }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
  return out;
}
