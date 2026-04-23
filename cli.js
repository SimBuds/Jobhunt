#!/usr/bin/env node

import { Command } from 'commander';
import { scrape } from './src/scrape.js';
import { analyze } from './src/analyze.js';
import { tailor } from './src/tailor.js';
import { generateCoverLetter } from './src/coverletter.js';
import { render } from './src/render.js';
import { autofill } from './src/autofill.js';
import { logApplication, listApplications, updateStatus } from './src/track.js';

const program = new Command();

program
  .name('job-apply-agent')
  .description('AI-powered job application toolkit')
  .version('1.0.0');

program
  .command('apply')
  .description('Scrape a job posting, tailor resume & cover letter, and optionally autofill')
  .argument('<url>', 'Job posting URL')
  .option('--no-autofill', 'Skip browser autofill')
  .option('--fast', 'Use qwen3.5:4b (faster) instead of gemma4:e2b for writing steps')
  .action(async (url, opts) => {
    const writingModel = opts.fast ? 'qwen3.5:4b' : 'gemma4:e2b';
    try {
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

      const id = logApplication({
        company: analysis.company_name,
        role: analysis.role_title,
        url,
        resumePath,
        coverletterPath: coverLetterPath,
      });
      console.log(`\nLogged as application #${id}`);

      if (opts.autofill) {
        console.log('\nLaunching browser for autofill...');
        await autofill(url, resumePath);
      }

      console.log('\nDone!');
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
