import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const CONFIG_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'data', 'config.json');

const DEFAULTS = Object.freeze({
  seniority_policy: 'filter',
  senior_score_cap: 30,
  max_pipeline_size: 200,
  verbose_scan: false,
});

const VALID_POLICIES = new Set(['filter', 'handicap', 'keep']);

let cached = null;

export async function loadConfig() {
  if (cached) return cached;
  let parsed = {};
  try {
    parsed = JSON.parse(await readFile(CONFIG_PATH, 'utf-8'));
  } catch {}
  const merged = { ...DEFAULTS, ...parsed };
  if (!VALID_POLICIES.has(merged.seniority_policy)) {
    merged.seniority_policy = DEFAULTS.seniority_policy;
  }
  cached = Object.freeze(merged);
  return cached;
}

export function resetConfigCache() {
  cached = null;
}

export { CONFIG_PATH, DEFAULTS };
