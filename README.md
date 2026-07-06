# Sabatini GitLab Draft Watcher

This version runs from a GitLab scheduled pipeline. It does not need a server, a Discord bot process, or a computer left on.

## How It Works

1. GitLab starts a scheduled pipeline.
2. The job fetches Sabatini's Untappd Business embed.
3. The script parses and normalizes the draft list.
4. It compares the current list to `data/state.json`.
5. If the list changed, it posts a GitHub-style diff to Discord using a webhook.
6. It commits the new `data/state.json` snapshot back to the GitLab repo.

## Files

- `.gitlab-ci.yml`: GitLab scheduled pipeline job.
- `src/check-once.js`: One-shot script used by the pipeline.
- `src/scrape.js`: Parses the Untappd Business menu HTML.
- `src/diff.js`: Builds Discord-safe diff code blocks.
- `data/state.json`: Created by the first successful run.

## GitLab Setup

### 1. Create A New GitLab Project

Create an empty project, then push this folder's contents to it.

### 2. Create A Discord Webhook

In Discord:

1. Open the target channel settings.
2. Go to Integrations.
3. Create a webhook.
4. Copy the webhook URL.

### 3. Create A GitLab Project Access Token

In GitLab:

1. Go to your project.
2. Open Settings > Access Tokens.
3. Create a project access token with `write_repository`.
4. Copy the token.

This lets the scheduled pipeline commit `data/state.json` after a detected change.

### 4. Add GitLab CI/CD Variables

In GitLab, go to Settings > CI/CD > Variables and add:

| Key | Value |
| --- | --- |
| `DISCORD_WEBHOOK_URL` | Your Discord webhook URL |
| `GITLAB_STATE_PUSH_TOKEN` | The project access token |
| `UNTAPPD_EMBED_URL` | Optional; defaults to the current Sabatini embed |
| `POST_INITIAL_SNAPSHOT` | Optional; set to `true` to post the first snapshot |

Keep the webhook and token masked/hidden when GitLab allows it.

### 5. Create The Schedule

In GitLab:

1. Go to Build > Pipeline schedules.
2. Select New schedule.
3. Use a cron interval like `17,47 * * * *` for twice an hour.
4. Set the target branch to your default branch.
5. Save it.

Use a non-round minute like `17` or `47` so the job is less likely to start at the same moment as everyone else's scheduled jobs.

### 6. Run It Once Manually

From Build > Pipeline schedules, select Run on your new schedule. The first run creates `data/state.json`. If `POST_INITIAL_SNAPSHOT=false`, it saves state but does not post to Discord until the next actual change.

## Local Test

```bash
npm install
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." npm run check-once
```

For a no-post local parse test:

```bash
npm install
npm run check
```
