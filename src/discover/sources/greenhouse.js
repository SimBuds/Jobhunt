import { fetchJson, stripHtml } from './_shared.js';

export const name = 'greenhouse';

async function fetchOne(slug, verbose) {
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

export async function fetchAll({ companies, verbose }) {
  const slugs = Array.isArray(companies.greenhouse) ? companies.greenhouse : [];
  if (!slugs.length) return { results: [], summary: null };

  const fetched = await Promise.all(slugs.map(s => fetchOne(s, verbose)));
  const ok = fetched.filter(r => r.ok).length;
  const skipped = fetched.length - ok;
  const results = fetched.flatMap(r => r.jobs);

  return {
    results,
    summary: `${ok} Greenhouse board${ok === 1 ? '' : 's'}` + (skipped ? ` (${skipped} skipped)` : ''),
  };
}
