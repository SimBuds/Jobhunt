import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { readPipeline } from './discover/scan.js';
import { readFeedbackSince } from './core/feedback.js';
import { listApplications } from './core/track.js';

const REPORT_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'data', 'report.md');

export { REPORT_PATH };

function formatMarkdown({ strong, skip, adjustments, submitted, topTargets, generated }) {
  const targets = topTargets.length
    ? topTargets.map((t, i) => `${i === 0 ? 'Top' : 'Next'} target: **${t.company}** — ${t.role} (${t.fit_score}%)`).join('\n- ')
    : '_No unapplied targets in the pipeline._';

  return `# Weekly report

_Generated ${generated}_

## This week

- **${strong}** strong matches found
- **${submitted}** applications submitted
- **${adjustments}** resume adjustments needed
- **${skip}** jobs worth skipping
${topTargets.length ? `- ${targets}` : `- ${targets}`}

---

Regenerate with \`npm run report\`.
`;
}

export async function report() {
  const pipeline = await readPipeline();
  const feedback = await readFeedbackSince(7);
  const apps = listApplications();

  const cutoff = Date.now() - 7 * 86_400_000;
  const recentApps = apps.filter(a => new Date(a.applied_at).getTime() > cutoff);

  const strong = pipeline.filter(j => j.priority === 'high' && !j.applied).length;
  const skip = pipeline.filter(j => j.priority === 'low' && !j.applied).length;
  const adjustments = feedback.filter(f => f.notes && f.notes !== '(none)').length;
  const submitted = feedback.filter(f => f.submitted).length || recentApps.length;

  const topTargets = [...pipeline]
    .filter(j => !j.applied && j.status !== 'stale')
    .sort((a, b) => b.fit_score - a.fit_score)
    .slice(0, 2);

  const summary = { strong, skip, adjustments, submitted, topTargets, generated: new Date().toISOString() };

  console.log('\nThis Week\n');
  console.log(`  ${strong} strong matches found`);
  console.log(`  ${submitted} applications submitted`);
  console.log(`  ${adjustments} resume adjustments needed`);
  console.log(`  ${skip} jobs worth skipping`);
  if (topTargets[0]) console.log(`  Top target: ${topTargets[0].company} ${topTargets[0].role} (${topTargets[0].fit_score}%)`);
  if (topTargets[1]) console.log(`  Next target: ${topTargets[1].company} ${topTargets[1].role} (${topTargets[1].fit_score}%)`);
  console.log();

  await writeFile(REPORT_PATH, formatMarkdown(summary), 'utf-8');
  console.log(`Written to ${REPORT_PATH}\n`);
}
