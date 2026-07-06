import { buildDiffMessages, buildInitialSnapshotMessages } from './diff.js';
import { scrapeDraftMenu } from './scrape.js';
import { loadState, saveState } from './state.js';

const config = {
  discordWebhookUrl: process.env.DISCORD_WEBHOOK_URL,
  untappdEmbedUrl:
    process.env.UNTAPPD_EMBED_URL ||
    'https://www-sabatinis-com.filesusr.com/html/78ef16_e5a731e6668aa7c1284a2b632b9ae06e.html',
  stateFile: process.env.STATE_FILE || 'data/state.json',
  postInitialSnapshot: parseBoolean(process.env.POST_INITIAL_SNAPSHOT),
};

if (!config.discordWebhookUrl) {
  throw new Error('Missing DISCORD_WEBHOOK_URL');
}

const previous = await loadState(config.stateFile);
const current = await scrapeDraftMenu(config.untappdEmbedUrl);

if (current.items.length === 0) {
  throw new Error(
    `Parsed 0 draft items from ${config.untappdEmbedUrl}. ` +
      'Check UNTAPPD_EMBED_URL; it should be the direct filesusr.com Untappd embed URL, not the Sabatini bottleshop page.'
  );
}

if (!previous) {
  await saveState(config.stateFile, current);
  console.log(`Saved initial snapshot with ${current.items.length} drafts.`);
  if (config.postInitialSnapshot) {
    await postMessages(buildInitialSnapshotMessages(current));
  }
  process.exit(0);
}

if (previous.hash === current.hash) {
  console.log(`No draft list change. Menu timestamp: ${current.updatedAt || 'unknown'}.`);
  process.exit(0);
}

const messages = buildDiffMessages(previous, current);
await postMessages(messages);
await saveState(config.stateFile, current);
console.log(`Posted ${messages.length} Discord message(s) and updated snapshot.`);

async function postMessages(messages) {
  for (const content of messages) {
    const response = await fetch(config.discordWebhookUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content }),
    });

    if (!response.ok) {
      const body = await response.text();
      throw new Error(`Discord webhook failed: ${response.status} ${response.statusText} ${body}`);
    }
  }
}

function parseBoolean(value) {
  return ['1', 'true', 'yes', 'y'].includes(String(value || '').toLowerCase());
}
