import { fetchJson, stripHtml } from './_shared.js';

export const name = 'lever';

async function fetchOne(slug, verbose) {
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

export async function fetchAll({ companies, verbose }) {
  const slugs = Array.isArray(companies.lever) ? companies.lever : [];
  if (!slugs.length) return { results: [], summary: null };

  const fetched = await Promise.all(slugs.map(s => fetchOne(s, verbose)));
  const ok = fetched.filter(r => r.ok).length;
  const skipped = fetched.length - ok;
  const results = fetched.flatMap(r => r.jobs);

  return {
    results,
    summary: `${ok} Lever board${ok === 1 ? '' : 's'}` + (skipped ? ` (${skipped} skipped)` : ''),
  };
}
