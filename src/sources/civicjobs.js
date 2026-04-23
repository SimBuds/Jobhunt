import { launchStealthBrowser, jitter } from './browser-search.js';

const MAX_QUERIES = 3;
const MAX_PER_QUERY = 25;

function buildUrl(query) {
  const params = new URLSearchParams({
    keywords: query,
    location: 'Toronto, ON',
  });
  return `https://www.civicjobs.ca/?${params}`;
}

export async function searchCivicJobs(queries) {
  const out = [];
  const clipped = queries.slice(0, MAX_QUERIES);
  if (!clipped.length) return out;

  const { browser, context } = await launchStealthBrowser({ headless: false });
  try {
    const page = await context.newPage();
    for (const q of clipped) {
      process.stderr.write(`[civicjobs] searching "${q}"\n`);
      try {
        await page.goto(buildUrl(q), { waitUntil: 'domcontentloaded', timeout: 25_000 });
        await page.waitForSelector('.job-item, article, .search-results, li a[href*="/jobs/"]', { timeout: 10_000 }).catch(() => {});

        const cards = await page.$$eval(
          '.job-item, article.job, li.job, .search-results article, .search-results li',
          nodes => nodes.map(n => {
            const linkEl = n.querySelector('a[href*="/jobs/"], a[href*="/job/"], h2 a, h3 a');
            const href = linkEl?.getAttribute('href') || '';
            const title = (n.querySelector('h2, h3, .job-title')?.textContent || linkEl?.textContent || '').trim();
            const company = n.querySelector('.employer, .company, .organization')?.textContent?.trim() || '';
            const location = n.querySelector('.location, .job-location')?.textContent?.trim() || '';
            const snippet = n.querySelector('.summary, .description, p')?.textContent?.trim() || '';
            return { href, title, company, location, snippet };
          })
        ).catch(() => []);

        const results = cards
          .filter(c => c.href && c.title)
          .slice(0, MAX_PER_QUERY)
          .map(c => ({
            company: c.company || 'CivicJobs',
            role: c.title,
            url: c.href.startsWith('http') ? c.href : `https://www.civicjobs.ca${c.href.startsWith('/') ? '' : '/'}${c.href}`,
            location: c.location,
            salary: '',
            tech_stack: [],
            ats_platform: 'civicjobs',
            description: `${c.title} at ${c.company}. ${c.location}. ${c.snippet}`,
          }));

        process.stderr.write(`[civicjobs] "${q}" -> ${results.length} jobs\n`);
        out.push(...results);
      } catch (err) {
        process.stderr.write(`[civicjobs] "${q}" failed: ${err.message}\n`);
      }
      await jitter(3000, 6000);
    }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
  return out;
}
