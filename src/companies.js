import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const COMPANIES_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'data', 'companies.json');

export { COMPANIES_PATH };

export async function loadCompanies() {
  try {
    const raw = await readFile(COMPANIES_PATH, 'utf-8');
    const parsed = JSON.parse(raw);
    return {
      greenhouse: Array.isArray(parsed.greenhouse) ? parsed.greenhouse : [],
      lever: Array.isArray(parsed.lever) ? parsed.lever : [],
    };
  } catch {
    return { greenhouse: [], lever: [] };
  }
}
