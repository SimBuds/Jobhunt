#!/usr/bin/env node

import { Command } from 'commander';
import { access } from 'node:fs/promises';
import { scrape } from './src/apply/scrape.js';
import { analyze } from './src/apply/analyze.js';
import { createHash } from 'node:crypto';
import { tailor, getResumePath } from './src/apply/tailor.js';
import { generateCoverLetter } from './src/apply/coverletter.js';
import { render } from './src/apply/render.js';
import { autofill } from './src/apply/autofill.js';
import { logApplication, listApplications, updateStatus } from './src/core/track.js';
import { scan, readPipeline, writePipeline } from './src/discover/scan.js';
import { report } from './src/report.js';
import { convertResume, autoDetectResumeFile } from './src/convert.js';
import { ask, askYesNo } from './src/core/prompt.js';
import { appendFeedback } from './src/core/feedback.js';

const program = new Command();

program
  .name('job-agent')
  .description('AI-assisted personal job search operating system')
  .version('2.1.0');

async function ensureBaseResume(profile = null) {
  try {
    await access(getResumePath(profile));
    return true;
  } catch {
    if (profile) {
      console.log(`\nProfile resume "base-resume.${profile}.json" not found at project root.`);
      console.log(`Create it first: cp base-resume.json base-resume.${profile}.json`);
      return false;
    }
  }
  const detected = await autoDetectResumeFile();
  if (detected) {
    console.log(`\nNo base-resume.json yet. Found "${detected.split('/').pop()}" at project root.`);
    if (await askYesNo('Convert it to base-resume.json now?')) {
      await convertResume(detected, { yes: false });
      try { await access(getResumePath()); return true; } catch { return false; }
    }
    console.log('OK — run `npm run convert -- <file>` when you\'re ready.');
    return false;
  }
  console.log('\nNo base-resume.json and no resume.pdf/.docx found at project root.');
  console.log('Run: npm run convert -- <path-to-resume>');
  return false;
}

async function runApplyFlow({ url, writingModel, noAutofill, pipelineEntry, profile = null }) {
  if (profile) console.log(`Using resume profile: ${profile}`);
  console.log('Scraping job posting...');
  const { description, platform } = await scrape(url);
  console.log(`Detected platform: ${platform}`);
  console.log(`Extracted ${description.length} chars of job description.\n`);

  console.log('Analyzing job requirements...');
  const analysis = await analyze(description);
  console.log(`\nCompany: ${analysis.company_name}`);
  console.log(`Role: ${analysis.role_title}`);
  console.log(`Keywords: ${analysis.keywords.join(', ')}`);
  console.log(`Requirements: ${analysis.requirements.length} found`);
  console.log(`Tone: ${analysis.tone}\n`);

  console.log(`Tailoring resume (${writingModel})...`);
  const tailoredResume = await tailor(analysis, { model: writingModel, profile });

  console.log(`\nGenerating cover letter (${writingModel})...`);
  const coverLetter = await generateCoverLetter(analysis, tailoredResume, { model: writingModel, description });

  console.log('\nRendering PDFs...');
  const { resumePath, coverLetterPath } = await render(tailoredResume, coverLetter, analysis.company_name);
  console.log(`Resume:       ${resumePath}`);
  console.log(`Cover Letter: ${coverLetterPath}`);

  const resumeHash = createHash('sha256').update(JSON.stringify(tailoredResume)).digest('hex').slice(0, 12);
  const appId = logApplication({
    company: analysis.company_name,
    role: analysis.role_title,
    url,
    resumePath,
    coverletterPath: coverLetterPath,
    resumeHash,
  });
  console.log(`\nLogged as application #${appId}`);

  if (!noAutofill) {
    console.log('\nLaunching browser for autofill...');
    await autofill(url, resumePath, { profile });
  }

  console.log('\n--- Application feedback ---');
  const submitted = await askYesNo('Did you complete and submit the application?');
  const notesPrompt = submitted
    ? 'What adjustments were needed? (press Enter to skip)'
    : 'What prevented submission? (press Enter to skip)';
  const notes = await ask(notesPrompt);

  await appendFeedback({
    company: analysis.company_name,
    role: analysis.role_title,
    url,
    submitted,
    notes,
  });

  if (pipelineEntry) {
    const pipeline = await readPipeline();
    const entry = pipeline.find(j => j.url === url);
    if (entry) {
      entry.applied = submitted;
      entry.status = submitted ? 'submitted' : 'attempted';
      if (notes) entry.notes = `${notes} | ${entry.notes || ''}`.slice(0, 400);
      await writePipeline(pipeline);
    }
  }

  updateStatus(appId, submitted ? 'submitted' : 'attempted');

  console.log('\nDone. Feedback saved to feedback.md');
  return { submitted };
}

async function pickCandidates(limit = 10, minScore = 0) {
  const pipeline = await readPipeline();
  return pipeline
    .filter(j => !j.applied && j.status !== 'stale' && j.fit_score >= minScore)
    .sort((a, b) => b.fit_score - a.fit_score)
    .slice(0, limit);
}

function printMenu(candidates) {
  console.log('\nTop unapplied roles:\n');
  candidates.forEach((j, i) => {
    console.log(
      `  ${String(i + 1).padStart(2)}. ${String(j.fit_score).padStart(3)}% — ${j.company} — ${j.role} [${j.ats_platform}]`
    );
  });
  console.log('\n   a. Apply to all listed above (one at a time, with confirm)');
  console.log('   q. Cancel\n');
}

program
  .command('scan')
  .description('Discover + auto-score jobs into applications/pipeline.json')
  .option('--sources <list>', 'Comma-separated: api,linkedin,jobbank,all', 'api')
  .option('--seniority <policy>', 'Override config: filter | handicap | keep')
  .action(async (opts) => {
    try {
      if (!(await ensureBaseResume())) return;
      const sources = opts.sources.split(',').map(s => s.trim()).filter(Boolean);
      const seniorityOverride = opts.seniority && ['filter', 'handicap', 'keep'].includes(opts.seniority)
        ? opts.seniority : null;
      if (opts.seniority && !seniorityOverride) {
        console.error(`Invalid --seniority "${opts.seniority}". Use filter, handicap, or keep.`);
        process.exit(1);
      }
      await scan({ sources, seniorityOverride });
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program
  .command('apply')
  .description('Guided apply: pick a job from the pipeline menu, or use --url')
  .option('--url <url>', 'Apply to a specific URL instead of the pipeline menu')
  .option('--limit <n>', 'Menu size (default 10)', '10')
  .option('--min-score <n>', 'Hide candidates below this fit score', '0')
  .option('--no-autofill', 'Skip browser autofill')
  .option('--fast', 'Use qwen3.5:4b for writing steps')
  .option('--profile <name>', 'Resume profile to use (loads base-resume.<name>.json)')
  .action(async (opts) => {
    const writingModel = opts.fast ? 'qwen3.5:4b' : 'gemma4:e2b';
    const noAutofill = !opts.autofill;
    const profile = opts.profile || null;
    try {
      if (!(await ensureBaseResume(profile))) return;

      if (opts.url) {
        await runApplyFlow({ url: opts.url, writingModel, noAutofill, pipelineEntry: false, profile });
        return;
      }

      const limit = Number.isInteger(+opts.limit) && +opts.limit > 0 ? +opts.limit : 10;
      const minScore = Number.isInteger(+opts.minScore) ? +opts.minScore : 0;
      const candidates = await pickCandidates(limit, minScore);
      if (!candidates.length) {
        console.log(`Nothing to apply to${minScore ? ` at >= ${minScore}% fit` : ''}. Run \`npm run scan\` first.`);
        return;
      }

      printMenu(candidates);
      const choice = (await ask(`Choose [1-${candidates.length}, a, q]:`)).toLowerCase().trim();

      if (!choice || choice === 'q') {
        console.log('Cancelled.');
        return;
      }

      if (choice === 'a') {
        for (let i = 0; i < candidates.length; i++) {
          const fresh = await readPipeline();
          const entry = fresh.find(j => j.url === candidates[i].url);
          if (entry?.applied) {
            console.log(`\nSkipping #${i + 1}: already applied.`);
            continue;
          }
          const j = candidates[i];
          console.log(`\n=== Job ${i + 1} of ${candidates.length} ===`);
          console.log(`${j.fit_score}% — ${j.company} — ${j.role}`);
          console.log(`URL: ${j.url}`);
          const ans = (await ask('Proceed with this one? (y/n to skip/q to cancel batch):')).toLowerCase().trim();
          if (ans === 'q') { console.log('Batch cancelled.'); break; }
          if (ans !== 'y' && ans !== 'yes') { console.log('Skipped.'); continue; }
          try {
            await runApplyFlow({ url: j.url, writingModel, noAutofill, pipelineEntry: true, profile });
          } catch (err) {
            console.error(`Error applying to ${j.company}: ${err.message}`);
            const cont = await askYesNo('Continue with the next job in the batch?');
            if (!cont) break;
          }
        }
        console.log('\nBatch complete.');
        return;
      }

      const n = parseInt(choice, 10);
      if (!Number.isInteger(n) || n < 1 || n > candidates.length) {
        console.log(`Invalid choice "${choice}".`);
        return;
      }
      const picked = candidates[n - 1];
      console.log(`\nSelected: ${picked.company} — ${picked.role} (${picked.fit_score}%)\n`);
      await runApplyFlow({ url: picked.url, writingModel, noAutofill, pipelineEntry: true, profile });
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program
  .command('report')
  .description('Weekly CLI summary of pipeline + applications')
  .action(async () => {
    try {
      await report();
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program
  .command('convert')
  .description('Convert resume (.pdf/.docx/.txt) into base-resume.json')
  .argument('[file]', 'Path to resume file (auto-detects resume.pdf/.docx if omitted)')
  .option('-y, --yes', 'Skip confirmation prompt')
  .action(async (file, opts) => {
    try {
      let target = file;
      if (!target) {
        target = await autoDetectResumeFile();
        if (!target) {
          console.log('No file given and no resume.pdf/.docx found at project root.');
          console.log('Usage: node cli.js convert <path-to-resume>');
          process.exit(1);
        }
        console.log(`Auto-detected: ${target}`);
      }
      await convertResume(target, { yes: opts.yes });
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program
  .command('list')
  .description('List all tracked applications')
  .action(() => {
    const apps = listApplications();
    if (!apps.length) {
      console.log('No applications tracked yet.');
      return;
    }
    console.log(`\n${'ID'.padEnd(5)} ${'Company'.padEnd(20)} ${'Role'.padEnd(25)} ${'Status'.padEnd(12)} ${'Date'.padEnd(12)}`);
    console.log('-'.repeat(74));
    for (const app of apps) {
      console.log(
        `${String(app.id).padEnd(5)} ${(app.company || '').padEnd(20).slice(0, 20)} ${(app.role || '').padEnd(25).slice(0, 25)} ${app.status.padEnd(12)} ${app.applied_at.slice(0, 10)}`
      );
    }
    console.log();
  });

program
  .command('status')
  .description('Update application status')
  .argument('<id>', 'Application ID')
  .argument('<new_status>', 'New status (e.g. rejected, interview, offer)')
  .action((id, newStatus) => {
    try {
      const app = updateStatus(Number(id), newStatus);
      console.log(`Updated application #${app.id} (${app.company} — ${app.role}) to "${app.status}"`);
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program.parse();
