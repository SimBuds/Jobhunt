import { readPipeline } from './scan.js';
import { readFeedbackSince } from './feedback.js';
import { listApplications } from './track.js';

export async function digest() {
  const pipeline = await readPipeline();
  const feedback = await readFeedbackSince(7);
  const apps = listApplications();

  const cutoff = Date.now() - 7 * 86_400_000;
  const recentApps = apps.filter(a => new Date(a.applied_at).getTime() > cutoff);

  const strong = pipeline.filter(j => j.priority === 'high' && !j.applied);
  const skip = pipeline.filter(j => j.priority === 'low' && !j.applied);
  const adjustments = feedback.filter(f => f.notes && f.notes !== '(none)').length;
  const submitted = feedback.filter(f => f.submitted).length || recentApps.length;

  const topTargets = [...pipeline]
    .filter(j => !j.applied && j.status !== 'stale')
    .sort((a, b) => b.fit_score - a.fit_score)
    .slice(0, 2);

  console.log('\nThis Week\n');
  console.log(`  ${strong.length} strong matches found`);
  console.log(`  ${submitted} applications submitted`);
  console.log(`  ${adjustments} resume adjustments needed`);
  console.log(`  ${skip.length} jobs worth skipping`);
  if (topTargets[0]) console.log(`  Top target: ${topTargets[0].company} ${topTargets[0].role} (${topTargets[0].fit_score}%)`);
  if (topTargets[1]) console.log(`  Next target: ${topTargets[1].company} ${topTargets[1].role} (${topTargets[1].fit_score}%)`);
  console.log();
}
