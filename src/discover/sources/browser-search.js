import { chromium } from 'playwright';
import { mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const REAL_UA =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

const PROFILE_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'data', 'browser-profile');

const LAUNCH_ARGS = [
  '--disable-blink-features=AutomationControlled',
  '--disable-features=IsolateOrigins,site-per-process',
  '--no-sandbox',
];

const CONTEXT_OPTS = {
  userAgent: REAL_UA,
  locale: 'en-CA',
  timezoneId: 'America/Toronto',
  viewport: { width: 1440, height: 900 },
  extraHTTPHeaders: { 'Accept-Language': 'en-CA,en;q=0.9' },
};

async function applyStealth(context) {
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-CA', 'en'] });
    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
  });
}

/**
 * Persistent-context launcher. Cookies + local storage persist between runs in
 * data/browser-profile/, so LinkedIn doesn't re-issue the same challenges
 * every scan. Returns { context, close } — no separate browser handle because
 * launchPersistentContext hides it.
 */
export async function launchStealthContext({ headless = false } = {}) {
  await mkdir(PROFILE_DIR, { recursive: true });
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless,
    args: LAUNCH_ARGS,
    ...CONTEXT_OPTS,
  });
  await applyStealth(context);
  return {
    context,
    close: async () => { try { await context.close(); } catch {} },
  };
}

/**
 * Ephemeral launcher. Used by public boards (Job Bank, CivicJobs) where
 * persisting cookies has no benefit.
 */
export async function launchStealthBrowser({ headless = false } = {}) {
  const browser = await chromium.launch({ headless, args: LAUNCH_ARGS });
  const context = await browser.newContext(CONTEXT_OPTS);
  await applyStealth(context);
  return { browser, context };
}

export function jitter(minMs, maxMs) {
  const ms = minMs + Math.random() * (maxMs - minMs);
  return new Promise(r => setTimeout(r, ms));
}

/**
 * Navigate with a single retry on HTTP 429. On second 429, return the failed
 * response so the caller can bail on the remaining queries.
 */
export async function gotoWithBackoff(page, url, { timeout = 25_000, label = 'fetch' } = {}) {
  const first = await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
  if (!first || first.status() !== 429) return first;
  process.stderr.write(`[${label}] HTTP 429; backing off 45s and retrying once...\n`);
  await jitter(45_000, 60_000);
  const second = await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
  if (second && second.status() === 429) {
    process.stderr.write(`[${label}] still rate-limited after retry; giving up this source.\n`);
  }
  return second;
}

const LOGIN_PATTERNS = /\b(sign[\s-]?in|log[\s-]?in|authwall|checkpoint|uas\/login|account\/login)\b/i;
const CAPTCHA_PATTERNS = /\b(captcha|hcaptcha|recaptcha|cloudflare|px-captcha|challenge-platform)\b/i;

export async function looksLikeLogin(page) {
  try {
    if (LOGIN_PATTERNS.test(page.url())) return true;
    const title = await page.title().catch(() => '');
    if (LOGIN_PATTERNS.test(title)) return true;
    return false;
  } catch { return false; }
}

export async function looksLikeCaptcha(page) {
  try {
    if (CAPTCHA_PATTERNS.test(page.url())) return true;
    const html = await page.content().catch(() => '');
    return CAPTCHA_PATTERNS.test(html.slice(0, 8000));
  } catch { return false; }
}
