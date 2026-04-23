import { launchStealthBrowser, jitter } from './browser-search.js';

const MAX_QUERIES = 3;
const MAX_PER_QUERY = 25;

function buildUrl(query) {
  // Indeed Canada radius is in km
  // sc=0kf%3Ajt(fulltime%2Cparttime%2Ccontract%2Ctemporary%2Cinternship)%3B tight-couples jobtype via filter param
  const params = new URLSearchParams({
    q: query,
    l: 'Toronto, ON',
    radius: '100',
    fromage: '14',
    sort: 'date',
  });
  return `https://ca.indeed.com/jobs?${params}`;
}

export async function searchIndeed(queries) {
  const out = [];
  const clipped = queries.slice(0, MAX_QUERIES);
  if (!clipped.length) return out;

  const { browser, context } = await launchStealthBrowser({ headless: false });
  try {
    const page = await context.newPage();
    for (const q of clipped) {
      process.stderr.write(`[indeed] searching "${q}"\n`);
      try {
        await page.goto(buildUrl(q), { waitUntil: 'domcontentloaded', timeout: 25_000 });
        await page.waitForSelector('#mosaic-jobResults, [data-testid="jobsearch-JobComponent"], .jobsearch-ResultsList', { timeout: 10_000 }).catch(() => {});

        const cards = await page.$$eval('.job_seen_beacon, [data-jk], [data-testid="jobsearch-JobComponent"]', nodes =>
          nodes.map(n => {
            const jk = n.getAttribute('data-jk')
              || n.querySelector('[data-jk]')?.getAttribute('data-jk')
              || n.querySelector('a.jcs-JobTitle')?.getAttribute('data-jk');
            const titleEl = n.querySelector('h2 a, a.jcs-JobTitle, h2.jobTitle a');
            const title = titleEl?.textContent?.trim() || '';
            const companyEl = n.querySelector('[data-testid="company-name"], .companyName');
            const company = companyEl?.textContent?.trim() || '';
            const locEl = n.querySelector('[data-testid="text-location"], .companyLocation');
            const location = locEl?.textContent?.trim() || '';
            const snippetEl = n.querySelector('[data-testid="job-snippet"], .job-snippet');
            const snippet = snippetEl?.textContent?.trim() || '';
            return { jk, title, company, location, snippet };
          })
        ).catch(() => []);

        const results = cards
          .filter(c => c.jk && c.title)
          .slice(0, MAX_PER_QUERY)
          .map(c => ({
            company: c.company,
            role: c.title,
            url: `https://ca.indeed.com/viewjob?jk=${c.jk}`,
            location: c.location,
            salary: '',
            tech_stack: [],
            ats_platform: 'indeed',
            description: `${c.title} at ${c.company}. ${c.location}. ${c.snippet}`,
          }));

        process.stderr.write(`[indeed] "${q}" -> ${results.length} jobs\n`);
        if (!results.length) {
          const url = page.url();
          if (/captcha|hcaptcha|cloudflare/i.test(url) || /captcha/i.test(await page.content().catch(() => ''))) {
            process.stderr.write('[indeed] CAPTCHA detected — please solve it in the browser to continue future runs.\n');
            break;
          }
        }
        out.push(...results);
      } catch (err) {
        process.stderr.write(`[indeed] "${q}" failed: ${err.message}\n`);
      }
      await jitter(4000, 8000);
    }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
  return out;
}
