import { Ollama } from 'ollama';
import { streamWithWatchdog, withRetry } from './_stream.js';

const ollama = new Ollama({ host: 'http://127.0.0.1:11434' });

export async function generateCoverLetter(analysis, tailoredResume, { model = 'gemma4:e2b' } = {}) {
  const prompt = `Write a 250-word cover letter for the "${analysis.role_title}" position at "${analysis.company_name}".

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

  process.stderr.write(`[coverletter] model=${model}\n`);
  const result = await withRetry(async () => {
    const response = await ollama.chat({
      model,
      messages: [{ role: 'user', content: prompt }],
      stream: true,
      think: false,
      options: { num_predict: 700 },
    });
    return streamWithWatchdog(response, 'coverletter');
  }, { label: 'coverletter' });
  return result.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
}
