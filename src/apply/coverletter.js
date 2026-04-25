import { Ollama } from 'ollama';
import { streamWithWatchdog, withRetry } from '../core/stream.js';

const ollama = new Ollama({ host: 'http://127.0.0.1:11434' });

const CLICHE_DENYLIST = [
  'dynamic environment', 'fast-paced environment', 'fast paced environment',
  'unique skill set', 'unique skillset', 'passionate about', 'team player',
  'go-getter', 'go getter', 'results-driven', 'results driven', 'synergy', 'synergies',
  'leverage', 'leveraging', 'cross-functional synergies', 'hit the ground running',
  'wealth of experience', 'adept at', 'driving results', 'optimization',
  'stakeholder', 'proactive', 'self-starter', 'self starter', 'detail-oriented',
  'detail oriented', 'strong communication skills', 'problem solver',
];

function detectCliches(text) {
  const lower = text.toLowerCase();
  return CLICHE_DENYLIST.filter(phrase => lower.includes(phrase));
}

export async function generateCoverLetter(analysis, tailoredResume, { model = 'qwen2.5-coder:7b', description = '' } = {}) {
  const normalizedTitle = (analysis.role_title || '').trim();
  if (!normalizedTitle) {
    throw new Error('Cannot generate cover letter: role_title is empty. Re-run analyze or check the scrape output.');
  }
  if (description) {
    const haystack = description.slice(0, 2000).toLowerCase();
    const needle = normalizedTitle.toLowerCase().replace(/\s+/g, ' ');
    const words = needle.split(' ').filter(w => w.length > 3);
    const matchCount = words.filter(w => haystack.includes(w)).length;
    if (words.length > 0 && matchCount < Math.ceil(words.length * 0.5)) {
      throw new Error(
        `role_title "${normalizedTitle}" not found in scraped job description — ` +
        `re-run analyze or check that the URL points to the correct posting.`
      );
    }
  }

  const prompt = `Write a 250-word cover letter for the "${normalizedTitle}" position at "${analysis.company_name}".

INSTRUCTIONS:
- Reference specific details from the job posting requirements
- Highlight my most relevant experience from my resume
- Match the tone: ${analysis.tone}
- Be genuine and specific — no generic filler
- Do NOT use placeholder brackets like [Your Name] — write it as plain text ready to send
- Use the name from my resume: ${tailoredResume.name || 'the applicant'}
- Output ONLY the cover letter text, no subject line, no markdown

KEY REQUIREMENTS TO ADDRESS:
${analysis.requirements.slice(0, 5).join('\n')}

MY RELEVANT KEYWORDS/SKILLS:
${analysis.keywords.join(', ')}

MY SUMMARY:
${tailoredResume.summary || ''}

MY EXPERIENCE HIGHLIGHTS:
${(tailoredResume.experience || []).slice(0, 2).map(e =>
    `${e.title} at ${e.company}: ${(e.bullets || []).slice(0, 3).join('; ')}`
  ).join('\n')}`;

  async function generate(messages) {
    const response = await ollama.chat({
      model,
      messages,
      stream: true,
      think: false,
      options: { num_predict: 700 },
    });
    const raw = await streamWithWatchdog(response, 'coverletter');
    return raw.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
  }

  process.stderr.write(`[coverletter] model=${model}\n`);
  const messages = [{ role: 'user', content: prompt }];
  let text = await withRetry(() => generate(messages), { label: 'coverletter' });

  const found = detectCliches(text);
  if (found.length) {
    process.stderr.write(`[coverletter] cliché phrases detected: ${found.join(', ')} — regenerating\n`);
    const retryMessages = [
      ...messages,
      { role: 'assistant', content: text },
      {
        role: 'user',
        content: `This draft contains cliché phrases that recruiters filter out. ` +
          `Rewrite it to be more specific and concrete, avoiding these phrases entirely: ${found.join(', ')}. ` +
          `Reference specific projects or metrics from my experience instead of generic adjectives.`
      },
    ];
    text = await withRetry(() => generate(retryMessages), { label: 'coverletter-retry' });
    const stillFound = detectCliches(text);
    if (stillFound.length) {
      throw new Error(
        `Cover letter still contains cliché phrases after retry: ${stillFound.join(', ')}. ` +
        `Try a different model or edit the output manually.`
      );
    }
  }

  return text;
}
