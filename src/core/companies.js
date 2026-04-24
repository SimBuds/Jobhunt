import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const COMPANIES_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'data', 'companies.json');

export { COMPANIES_PATH };

const DEFAULT_QUERIES = [
  'software engineer',
  'frontend developer',
  'backend developer',
];

const QUERY_FIELDS = [
  'linkedin_queries',
  'jobbank_queries',
];

function pickQueries(parsed, field) {
  return Array.isArray(parsed?.[field]) && parsed[field].length
    ? parsed[field]
    : DEFAULT_QUERIES;
}

export async function loadCompanies() {
  let parsed = null;
  try {
    parsed = JSON.parse(await readFile(COMPANIES_PATH, 'utf-8'));
  } catch {}
  const out = {
    greenhouse: Array.isArray(parsed?.greenhouse) ? parsed.greenhouse : [],
    lever: Array.isArray(parsed?.lever) ? parsed.lever : [],
  };
  for (const f of QUERY_FIELDS) out[f] = pickQueries(parsed, f);
  return out;
}
