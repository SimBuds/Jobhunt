#!/usr/bin/env node

import { Command } from 'commander';
import { scrape } from './src/scrape.js';
import { analyze } from './src/analyze.js';
import { tailor } from './src/tailor.js';
import { generateCoverLetter } from './src/coverletter.js';
import { render } from './src/render.js';
import { autofill } from './src/autofill.js';
import { logApplication, listApplications, updateStatus } from './src/track.js';
import { scan } from './src/scan.js';
import { digest } from './src/digest.js';
import { importResume } from './src/import-resume.js';
import { readPipeline, writePipeline } from './src/scan.js';
import { ask, askYesNo } from './src/prompt.js';
import { appendFeedback } from './src/feedback.js';

const program = new Command();

program
  .name('job-agent')
  .description('AI-assisted personal job search operating system')
  .version('2.0.0');

async function runApplyFlow({ url, writingModel, noAutofill, pipelineEntry }) {
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
  const tailoredResume = await tailor(analysis, { model: writingModel });

  console.log(`\nGenerating cover letter (${writingModel})...`);
  const coverLetter = await generateCoverLetter(analysis, tailoredResume, { model: writingModel });

  console.log('\nRendering PDFs...');
  const { resumePath, coverLetterPath } = await render(tailoredResume, coverLetter, analysis.company_name);
  console.log(`Resume:       ${resumePath}`);
  console.log(`Cover Letter: ${coverLetterPath}`);

  const appId = logApplication({
    company: analysis.company_name,
    role: analysis.role_title,
    url,
    resumePath,
    coverletterPath: coverLetterPath,
  });
  console.log(`\nLogged as application #${appId}`);

  let autofillResult = { platform, filled: false };
  if (!noAutofill) {
    console.log('\nLaunching browser for autofill...');
    autofillResult = await autofill(url, resumePath);
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

  if (submitted) {
    updateStatus(appId, 'submitted');
  } else {
    updateStatus(appId, 'attempted');
  }

  console.log('\nDone. Feedback saved to feedback.md');
}

program
  .command('scan')
  .description('Discover + auto-score jobs from configured Greenhouse/Lever boards')
  .action(async () => {
    try {
      await scan();
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program
  .command('apply')
  .description('Guided apply flow: picks highest-fit unapplied job, or a specific --url')
  .option('--url <url>', 'Apply to a specific URL instead of the pipeline top')
  .option('--no-autofill', 'Skip browser autofill')
  .option('--fast', 'Use qwen3.5:4b for writing steps')
  .action(async (opts) => {
    const writingModel = opts.fast ? 'qwen3.5:4b' : 'gemma4:e2b';
    try {
      let url = opts.url;
      let fromPipeline = false;
      if (!url) {
        const pipeline = await readPipeline();
        const candidate = pipeline
          .filter(j => !j.applied && j.status !== 'stale')
          .sort((a, b) => b.fit_score - a.fit_score)[0];
        if (!candidate) {
          console.log('Nothing to apply to. Run `job-agent scan` first, or pass --url.');
          return;
        }
        url = candidate.url;
        fromPipeline = true;
        console.log(`Next target: ${candidate.company} — ${candidate.role} (${candidate.fit_score}% fit)`);
        console.log(`URL: ${url}\n`);
        if (!(await askYesNo('Proceed?'))) {
          console.log('Cancelled.');
          return;
        }
      }
      await runApplyFlow({
        url,
        writingModel,
        noAutofill: !opts.autofill,
        pipelineEntry: fromPipeline,
      });
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program
  .command('digest')
  .description('Weekly CLI summary of pipeline + applications')
  .action(async () => {
    try {
      await digest();
    } catch (err) {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    }
  });

program
  .command('import-resume')
  .description('Convert resume.pdf/.docx/.txt into base-resume.json')
  .argument('<file>', 'Path to resume file (.pdf, .docx, .txt, .md)')
  .option('-y, --yes', 'Skip confirmation prompt')
  .action(async (file, opts) => {
    try {
      await importResume(file, { yes: opts.yes });
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
