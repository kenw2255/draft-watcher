import { diffLines } from 'diff';

const MAX_DISCORD_MESSAGE_LENGTH = 1900;

export function buildDiffMessages(previous, current) {
  const header = [
    'Sabatini draft list changed',
    `Updated: ${current.updatedAt || 'unknown'}`,
    `Source: ${current.sourceUrl}`,
  ].join('\n');

  const diffBody = toUnifiedDiff(previous.lines, current.lines, current.title || 'Beers on Draft');
  return chunkCodeBlock(`${header}\n\n${diffBody}`, 'diff');
}

export function buildInitialSnapshotMessages(snapshot) {
  const body = [
    'Sabatini draft list snapshot',
    `Updated: ${snapshot.updatedAt || 'unknown'}`,
    `Source: ${snapshot.sourceUrl}`,
    '',
    ...snapshot.lines.map((line) => `+ ${line}`),
  ].join('\n');

  return chunkCodeBlock(body, 'diff');
}

function toUnifiedDiff(previousLines, currentLines, title) {
  const previousText = `${previousLines.join('\n')}\n`;
  const currentText = `${currentLines.join('\n')}\n`;
  const parts = diffLines(previousText, currentText);
  const rows = ['--- previous', '+++ current', `@@ ${title}`];

  for (const part of parts) {
    const prefix = part.added ? '+' : part.removed ? '-' : ' ';
    const lines = part.value.split('\n').filter(Boolean);
    rows.push(...lines.map((line) => `${prefix} ${line}`));
  }

  return rows.join('\n');
}

function chunkCodeBlock(text, language) {
  const prefix = `\`\`\`${language}\n`;
  const suffix = '\n```';
  const limit = MAX_DISCORD_MESSAGE_LENGTH - prefix.length - suffix.length;
  const chunks = [];
  let current = '';

  for (const line of text.split('\n')) {
    const next = current ? `${current}\n${line}` : line;
    if (next.length > limit) {
      if (current) chunks.push(current);
      current = line;
    } else {
      current = next;
    }
  }

  if (current) chunks.push(current);
  return chunks.map((chunk) => `${prefix}${chunk}${suffix}`);
}
