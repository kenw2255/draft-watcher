# Sabatini Draft Watcher

A GitHub Actions watcher for Sabatini's rotating draft menu. The scheduled check fetches Untappd's public embed script, scans its known menu markers with Python's standard library, compares the result with `data/state.json`, and posts a `diff` code block to Discord when the draft list changes.

`watch.py` is a dependency-free marker scanner tailored to Untappd's generated menu markup.

- `fetch_menu_html` loads the complete menu without launching a browser.
- `parse_menu_html` scans the current Untappd markup into Python objects.
- The state, diff, and Discord functions handle the notification workflow.

The Untappd embed endpoint returns the complete rendered menu inside a JavaScript string. The watcher requests the response with gzip compression, then decodes that string directly, so it does not need Playwright, Chromium, or a browser-rendering service. If the endpoint returns an uncompressed response, the same parsing path continues normally.

Each check records transferred and decompressed response sizes plus fetch, HTML decoding, parsing, and total menu-read durations in the GitHub Actions log. This diagnostic information is not sent to Discord or saved in the snapshot.

## Discord output settings

Near the top of `watch.py`, set any optional beer detail to `True` or `False`:

```python
SHOW_STYLE = True
SHOW_ABV = True
SHOW_IBU = True
SHOW_BREWERY = True
SHOW_LOCATION = True
SHOW_SIZES_AND_PRICES = False
```

The beer name is always included. Changing one of these settings changes the normalized snapshot, so the next run may post one diff reflecting the new format.

These settings affect only the normalized `lines` used for Discord messages. Every parsed field is still stored under `items` in `data/state.json`. If a hidden field changes, the workflow updates and commits the full JSON snapshot without posting anything to Discord.

## GitHub Actions

Add a repository secret named `DISCORD_WEBHOOK_URL` under **Settings > Secrets and variables > Actions > Secrets > New repository secret**. Paste the Discord webhook URL as its value. The Sabatini Untappd URL is defined directly in `watch.py`, so no Untappd secret or repository variable is needed.

The workflow requests `contents: write` permission so GitHub's built-in Actions token can update `watcher-state`. No personal access token is needed for state commits. If the Discord secret has not been added yet, scheduled and manual runs finish successfully with a warning and do not check the menu.

The workflow runs hourly at the top of the hour from 10 AM through 2 AM EST (bar hours) and can also be started manually.

Both workflows use the `python3` runtime already installed on GitHub's `ubuntu-latest` runner and have no package-install step.

### Post the full menu manually

The separate **Post full Sabatini draft list** workflow runs only when started from the GitHub Actions page. It loads `data/state.json` from `watcher-state` and posts every beer in that saved snapshot.

This manual workflow does not check the Untappd website and does not write anything back to `watcher-state`, so the regular hourly workflow continues from the same saved state.

The first run always posts the full menu and saves it to `data/state.json`. Every later run posts only genuinely removed and added beer lines, with no unchanged menu items included. Moving an existing beer to another draft position is not treated as a removal and addition, although the latest website order is still saved in JSON. A missing, blank, empty, or manually deleted state file counts as a first run, even when the `watcher-state` branch already exists.

The website's `Updated` timestamp can also trigger a notification. It appears once as a plain `Updated:` line and is not included in the beer addition or removal totals. If Untappd changes only that timestamp, Discord receives a small `+0`/`-0` notification with no beer lines.

If Untappd's response contains no parseable beers, the watcher posts an error to Discord, leaves the previous snapshot unchanged, and fails with a dedicated parse-error status. The Cloudflare dispatcher remains enabled, so fix or verify the embed before the next scheduled run.

Discord messages include the menu title and current website timestamp, but omit the fixed source URL. The URL remains stored in `data/state.json`.

For consistent Discord mobile highlighting, internal ASCII hyphens are displayed as en dashes. The real leading `- ` on a removed beer remains an ASCII diff marker, so genuine removals still color the full line red. This formatting happens only in Discord output; JSON retains the original Untappd text.

Webhook requests use Discord's required `DiscordBot (URL, version)` User-Agent format. In GitHub Actions, the URL is built from the predefined `GITHUB_SERVER_URL` and `GITHUB_REPOSITORY` variables; this avoids Cloudflare error 1010 blocking Python's default `urllib` signature.

## Separate state branch

Code lives on `main`. Snapshot commits live on the orphan `watcher-state` branch, which is used as storage and is never merged into `main`.

`data/state.json` remains in `.gitignore` on `main` because the workflow loads a temporary copy into its checkout. The save helper explicitly commits that file from a separate `watcher-state` worktree.

The workflow helpers handle this automatically:

1. `ci/load-state.sh` loads `data/state.json` from `watcher-state` before the watcher runs.
2. `watch.py` checks the current menu and updates the local state when needed.
3. `ci/save-state.sh` creates the orphan branch if necessary and commits only `data/state.json` to it.

The workflow compares the state file before and after the menu check. When the snapshot is unchanged, it skips the additional state-branch fetch and temporary worktree used by the save helper.

The workflow uses a concurrency group so two runs cannot update the state branch at the same time.

## Raw HTML hash shortcut

`watch.py` stores a SHA-256 hash of the decoded raw menu HTML as `rawHtmlHash` and enables this shortcut:

```python
SKIP_PARSE_WHEN_RAW_HTML_UNCHANGED = True
```

When the raw hash matches the saved snapshot, the watcher skips its marker scan and exits without posting or saving.

Hashing does not avoid the network request or HTML decoding, but it avoids the normal menu scan when the response is unchanged.

The first run after this addition parses normally and saves `rawHtmlHash` into the existing snapshot. No manual JSON edit is required.
