import { chromium } from 'playwright';
import { loadBaseResume } from './tailor.js';
import { detectPlatform } from './scrape.js';

async function safeFill(page, selector, value, timeout = 2500) {
  if (!value) return false;
  try {
    await page.fill(selector, value, { timeout });
    return true;
  } catch {
    return false;
  }
}

async function tryUpload(page, resumePath, selector = 'input[type="file"]') {
  if (!resumePath) return;
  try {
    const fileInput = await page.$(selector);
    if (fileInput) await fileInput.setInputFiles(resumePath);
  } catch { /* no upload field yet */ }
}

async function fillGreenhouse(page, resume, resumePath) {
  const first = resume.name?.split(' ')[0];
  const last = resume.name?.split(' ').slice(1).join(' ');
  await safeFill(page, '#first_name', first);
  await safeFill(page, '#last_name', last);
  await safeFill(page, '#email', resume.email);
  await safeFill(page, '#phone', resume.phone);

  const customFields = [
    { pattern: /linkedin/i, value: resume.linkedin },
    { pattern: /github/i, value: resume.github },
    { pattern: /website|portfolio/i, value: resume.website },
  ];
  const inputs = await page.$$('input[type="text"], input[type="url"]');
  for (const input of inputs) {
    const label = await input.evaluate(el => {
      const lab = el.closest('.field')?.querySelector('label');
      return lab?.textContent || el.getAttribute('placeholder') || '';
    });
    for (const cf of customFields) {
      if (cf.value && cf.pattern.test(label)) await input.fill(cf.value);
    }
  }

  await tryUpload(page, resumePath);
}

async function fillLever(page, resume, resumePath) {
  const mappings = [
    ['input[name="name"]', resume.name],
    ['input[name="email"]', resume.email],
    ['input[name="phone"]', resume.phone],
    ['input[name="urls[LinkedIn]"]', resume.linkedin],
    ['input[name="urls[GitHub]"]', resume.github],
    ['input[name="urls[Portfolio]"]', resume.website],
  ];
  for (const [sel, val] of mappings) await safeFill(page, sel, val);
  await tryUpload(page, resumePath);
}

async function fillAshby(page, resume, resumePath) {
  const mappings = [
    ['input[name="_systemfield_name"]', resume.name],
    ['input[name="name"]', resume.name],
    ['input[name="_systemfield_email"]', resume.email],
    ['input[name="email"]', resume.email],
    ['input[name="_systemfield_phone"]', resume.phone],
    ['input[name="phone"]', resume.phone],
    ['input[aria-label*="LinkedIn" i]', resume.linkedin],
    ['input[aria-label*="GitHub" i]', resume.github],
    ['input[aria-label*="Website" i]', resume.website],
  ];
  for (const [sel, val] of mappings) await safeFill(page, sel, val);
  await tryUpload(page, resumePath);
}

async function fillSmartRecruiters(page, resume, resumePath) {
  const first = resume.name?.split(' ')[0];
  const last = resume.name?.split(' ').slice(1).join(' ');
  const mappings = [
    ['input[name="firstName"]', first],
    ['input[name="lastName"]', last],
    ['input[name="email"]', resume.email],
    ['input[name="phoneNumber"]', resume.phone],
    ['input[name="linkedinProfile"]', resume.linkedin],
    ['input[name="website"]', resume.website],
  ];
  for (const [sel, val] of mappings) await safeFill(page, sel, val);
  await tryUpload(page, resumePath);
}

async function fillWorkday(page, resume, resumePath) {
  // Workday is a multi-step wizard. Most postings land on a "View Job" page with an Apply button;
  // after Apply and sign-in, fields have data-automation-id attributes.
  // We do best-effort: click Apply if visible, then fill any known fields on the current page.
  try {
    const applyBtn = await page.$('[data-automation-id="adventureButton"], button:has-text("Apply")');
    if (applyBtn) {
      await applyBtn.click({ timeout: 2000 });
      await page.waitForTimeout(1500);
    }
  } catch { /* no Apply button yet */ }

  const first = resume.name?.split(' ')[0];
  const last = resume.name?.split(' ').slice(1).join(' ');
  const mappings = [
    ['[data-automation-id="legalNameSection_firstName"]', first],
    ['[data-automation-id="legalNameSection_lastName"]', last],
    ['[data-automation-id="email"]', resume.email],
    ['input[data-automation-id="phone-number"]', resume.phone],
    ['[data-automation-id="phone-number"]', resume.phone],
  ];
  for (const [sel, val] of mappings) await safeFill(page, sel, val);

  await tryUpload(page, resumePath, '[data-automation-id="file-upload-input-ref"]');
  await tryUpload(page, resumePath);

  console.log('\nWorkday autofill is best-effort — the form spans multiple pages.');
  console.log('You may need to sign in / create an account first, then re-run fields manually on later pages.');
}

const FILLERS = {
  greenhouse: fillGreenhouse,
  lever: fillLever,
  ashby: fillAshby,
  smartrecruiters: fillSmartRecruiters,
  workday: fillWorkday,
};

export async function autofill(url, resumePath, { profile = null } = {}) {
  const resume = await loadBaseResume(profile);
  const platform = detectPlatform(url);
  const filler = FILLERS[platform];
  let filled = false;

  if (!filler) {
    console.log(`Autofill has no template for "${platform}". Opening page without autofill.`);
  }

  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });

  if (filler) {
    try {
      await filler(page, resume, resumePath);
      filled = true;
    } catch (err) {
      console.log(`Autofill error (non-fatal): ${err.message}`);
    }
  }

  console.log('\nBrowser is open with pre-filled fields.');
  console.log('Review the form and click Submit when ready.');
  console.log('Close the browser window when done.\n');

  await new Promise(resolve => {
    const done = () => resolve();
    browser.once('disconnected', done);
    context.once('close', done);
  });
  try { await browser.close(); } catch {}

  return { platform, filled };
}
