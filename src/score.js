const STOP = new Set([
  'the','and','for','with','you','your','are','our','a','an','to','of','in','on','at','by','or','as','is','be','we',
  'will','from','this','that','have','has','but','not','it','its','their','they','them','who','what','when','where',
  'able','ability','experience','required','preferred','work','working','role','team','teams','product','products',
  'company','candidate','ideal','strong','knowledge','skills','skill','including','etc','using','use','used','build',
  'building','developer','engineer','software','years','year','plus','bonus','nice','must','should','across','join'
]);

const STACK_TOKENS = [
  'react','nextjs','next.js','vue','svelte','angular','node','nodejs','express','typescript','javascript','python',
  'django','flask','fastapi','ruby','rails','go','golang','rust','java','spring','kotlin','swift','php','laravel',
  'graphql','rest','grpc','postgres','postgresql','mysql','mongodb','redis','dynamodb','elasticsearch','kafka',
  'aws','gcp','azure','docker','kubernetes','terraform','ci/cd','jenkins','github actions','shopify','hubspot',
  'contentful','wordpress','liquid','tailwind','sass','webpack','vite','jest','playwright','cypress','html','css'
];

const SENIOR_WORDS = /(senior|sr\.|staff|principal|lead|manager|director|vp|head of|architect)/i;
const JUNIOR_WORDS = /(intern|internship|new grad|new-grad|entry|junior|jr\.|early[-\s]?career|i\b|ii\b|associate)/i;

function tokenize(text = '') {
  return (text.toLowerCase().match(/[a-z][a-z0-9+.#/-]{1,}/g) || [])
    .filter(t => !STOP.has(t) && t.length > 2);
}

function extractStack(text = '') {
  const lc = text.toLowerCase();
  return STACK_TOKENS.filter(t => lc.includes(t));
}

function resumeBag(resume) {
  const text = [
    resume.summary || '',
    ...(resume.skills || []),
    ...(resume.experience || []).flatMap(e => [e.title, ...(e.bullets || [])]),
    ...(resume.education || []).flatMap(e => [e.degree || '', e.notes || '']),
  ].join(' ');
  return {
    text,
    tokens: new Set(tokenize(text)),
    stack: new Set(extractStack(text)),
  };
}

export function score(job, resume) {
  const jdText = `${job.title || ''} ${job.description || ''}`;
  const jdTokens = tokenize(jdText);
  const jdTokenSet = new Set(jdTokens);
  const jdStack = new Set(extractStack(jdText));
  const r = resumeBag(resume);

  const keywordOverlap = [...jdTokenSet].filter(t => r.tokens.has(t));
  const keywordPts = Math.min(40, keywordOverlap.length * 1.2);

  const stackOverlap = [...jdStack].filter(t => r.stack.has(t));
  const stackPts = jdStack.size === 0 ? 10 : Math.min(20, (stackOverlap.length / jdStack.size) * 20);

  const title = job.title || '';
  let titlePts = 10;
  let seniorityMultiplier = 1;
  if (JUNIOR_WORDS.test(title)) titlePts = 20;
  else if (SENIOR_WORDS.test(title)) { titlePts = 0; seniorityMultiplier = 0.5; }

  const educationPts = /computer|programming|software|engineering|science/i.test(r.text) ? 10 : 5;

  const jdKeyTerms = [...jdStack, ...keywordOverlap.slice(0, 20)];
  const atsPts = jdKeyTerms.length
    ? Math.min(10, (keywordOverlap.length / Math.max(1, jdTokens.length)) * 200)
    : 5;

  const total = Math.round((keywordPts + stackPts + titlePts + educationPts + atsPts) * seniorityMultiplier);

  const missing_keywords = [...jdStack].filter(t => !r.stack.has(t)).slice(0, 8);
  const rationale =
    `keywords ${Math.round(keywordPts)}/40, stack ${Math.round(stackPts)}/20 (${stackOverlap.length}/${jdStack.size}), ` +
    `title ${titlePts}/20, edu ${educationPts}/10, ats ${Math.round(atsPts)}/10`;

  return {
    score: Math.min(100, Math.max(0, total)),
    missing_keywords,
    rationale,
  };
}

export function priorityFor(score) {
  if (score >= 80) return 'high';
  if (score >= 60) return 'medium';
  return 'low';
}
