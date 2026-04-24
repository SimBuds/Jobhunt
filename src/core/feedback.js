import { appendFile, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const FEEDBACK_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', 'feedback.md');

export { FEEDBACK_PATH };

export async function appendFeedback({ company, role, url, submitted, notes }) {
  const date = new Date().toISOString().slice(0, 10);
  const block =
    `\n## ${date} — ${company || 'Unknown'} — ${role || 'Unknown'}\n` +
    `- URL: ${url}\n` +
    `- Submitted: ${submitted ? 'yes' : 'no'}\n` +
    `- Notes: ${notes?.trim() ? notes.trim() : '(none)'}\n`;
  await appendFile(FEEDBACK_PATH, block, 'utf-8');
}

export async function readFeedbackSince(days = 7) {
  let raw;
  try {
    raw = await readFile(FEEDBACK_PATH, 'utf-8');
  } catch {
    return [];
  }
  const cutoff = Date.now() - days * 86_400_000;
  const blocks = raw.split(/^## /m).filter(Boolean);
  const entries = [];
  for (const b of blocks) {
    const m = b.match(/^(\d{4}-\d{2}-\d{2})\s+—\s+(.+?)\s+—\s+(.+)$/m);
    if (!m) continue;
    const [, date, company, role] = m;
    if (new Date(date).getTime() < cutoff) continue;
    const submitted = /- Submitted:\s*yes/i.test(b);
    const notesMatch = b.match(/- Notes:\s*(.+)$/m);
    const notes = notesMatch ? notesMatch[1].trim() : '';
    entries.push({ date, company, role, submitted, notes });
  }
  return entries;
}
