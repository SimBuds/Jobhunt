export async function withRetry(fn, { label, retries = 1 } = {}) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await fn(attempt);
    } catch (err) {
      lastErr = err;
      const retryable = /aborted|stalled|ECONN|timed out|fetch failed/i.test(err.message);
      if (attempt < retries && retryable) {
        process.stderr.write(`[${label}] attempt ${attempt + 1} failed (${err.message}); retrying...\n`);
        continue;
      }
      throw err;
    }
  }
  throw lastErr;
}

export async function streamWithWatchdog(response, label, { stallMs = 45_000, maxMs = 300_000 } = {}) {
  let result = '';
  let lastChunk = Date.now();
  let aborted = null;

  const abort = (reason) => {
    if (aborted) return;
    aborted = reason;
    try { response.abort(); } catch {}
  };

  const watchdog = setInterval(() => {
    if (Date.now() - lastChunk > stallMs) {
      abort(`stalled >${stallMs}ms with no new tokens`);
    }
  }, 2_000);
  const maxTimer = setTimeout(() => abort(`exceeded max ${maxMs}ms`), maxMs);

  try {
    for await (const chunk of response) {
      lastChunk = Date.now();
      const text = chunk.message?.content ?? '';
      process.stderr.write(text);
      result += text;
    }
  } finally {
    clearInterval(watchdog);
    clearTimeout(maxTimer);
    process.stderr.write('\n');
  }

  if (aborted) {
    throw new Error(`[${label}] aborted: ${aborted}. Partial output: ${result.length} chars`);
  }
  return result;
}
