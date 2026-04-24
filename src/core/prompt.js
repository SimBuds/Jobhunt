import readline from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';

export async function ask(question) {
  const rl = readline.createInterface({ input, output });
  try {
    return (await rl.question(`${question} `)).trim();
  } finally {
    rl.close();
  }
}

export async function askYesNo(question) {
  const answer = await ask(`${question} (y/n)`);
  return /^y(es)?$/i.test(answer);
}
