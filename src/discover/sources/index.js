import * as greenhouse from './greenhouse.js';
import * as lever from './lever.js';
import * as linkedin from './linkedin.js';
import * as jobbank from './jobbank.js';

// Each source module exports:
//   - name: string
//   - fetchAll({ companies, verbose }) -> { results, summary }
export const REGISTRY = {
  [greenhouse.name]: greenhouse,
  [lever.name]: lever,
  [linkedin.name]: linkedin,
  [jobbank.name]: jobbank,
};

// Aliases that fan out to multiple sources.
const ALIASES = {
  api: ['greenhouse', 'lever'],
  all: ['greenhouse', 'lever', 'linkedin', 'jobbank'],
};

export function expandSources(requested) {
  const out = [];
  const seen = new Set();
  for (const s of requested) {
    const expanded = ALIASES[s] || [s];
    for (const name of expanded) {
      if (seen.has(name)) continue;
      if (!REGISTRY[name]) {
        throw new Error(`Unknown source "${name}". Known: ${Object.keys(REGISTRY).join(', ')} (aliases: ${Object.keys(ALIASES).join(', ')}).`);
      }
      seen.add(name);
      out.push(name);
    }
  }
  return out;
}

// Sources that open a browser / scrape rather than hit a JSON API.
const SCRAPER_SOURCES = new Set(['linkedin', 'jobbank']);

export function isScraperSource(name) {
  return SCRAPER_SOURCES.has(name);
}
