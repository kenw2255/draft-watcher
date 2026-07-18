# Sabatini Python Draft Watcher

A small, readable GitHub Actions watcher for Sabatini's rotating draft menu. It renders the JavaScript-powered Untappd embed with Playwright, parses the menu with Beautiful Soup, compares it with `data/state.json`, and posts a `diff` code block to Discord when the draft list changes.

The code is intentionally kept in one file, `watch.py`, but is divided into a few clear pieces:

- `Settings`, `DraftBeer`, and `MenuSnapshot` describe the data.
- `render_menu_page` loads the dynamic menu in Chromium.
- `parse_menu_html` and `parse_beer` turn the HTML into Python objects.
- The state, diff, and Discord functions handle the notification workflow.

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

Configure the repository with:

- a repository secret named `DISCORD_WEBHOOK_URL`
- optional repository variable `UNTAPPD_EMBED_URL`

Add the webhook under **Settings > Secrets and variables > Actions > Secrets > New repository secret**. Paste the Discord webhook URL as the value and use `DISCORD_WEBHOOK_URL` as the name. The optional Untappd override belongs on the **Variables** tab, but the correct Sabatini URL is already the default in `watch.py`.

The workflow requests `contents: write` permission so GitHub's built-in Actions token can update `watcher-state`. No personal access token is needed for state commits. If the Discord secret has not been added yet, scheduled and manual runs finish successfully with a warning and do not check the menu.

The workflow runs hourly at 17 minutes past the hour and can also be started manually:

```cron
17 * * * *
```

The first run always posts the full menu and saves it to `data/state.json`. Every later run posts only genuinely removed and added beer lines, with no unchanged menu items included. Moving an existing beer to another draft position is not treated as a removal and addition, although the latest website order is still saved in JSON. A missing, blank, empty, or manually deleted state file counts as a first run, even when the `watcher-state` branch already exists.

The website's `Updated` timestamp can also trigger a notification. It appears once as a plain `Updated:` line and is not included in the beer addition or removal totals. If Untappd changes only that timestamp, Discord receives a small `+0`/`-0` notification with no beer lines.

Discord messages include the menu title and current website timestamp, but omit the fixed source URL. The URL remains stored in `data/state.json`.

For consistent Discord mobile highlighting, internal ASCII hyphens are displayed as en dashes. The real leading `- ` on a removed beer remains an ASCII diff marker, so genuine removals still color the full line red. This formatting happens only in Discord output; JSON retains the original Untappd text.

Webhook requests use Discord's required `DiscordBot (URL, version)` User-Agent format. In GitHub Actions, the URL is built from the predefined `GITHUB_SERVER_URL` and `GITHUB_REPOSITORY` variables; this avoids Cloudflare error 1010 blocking Python's default `urllib` signature.

## Separate state branch

Code lives on `main`. Snapshot commits live on the orphan `watcher-state` branch, which is used as storage and is never merged into `main`.

The workflow helpers handle this automatically:

1. `ci/load-state.sh` loads `data/state.json` from `watcher-state` before the watcher runs.
2. `watch.py` checks the current menu and updates the local state when needed.
3. `ci/save-state.sh` creates the orphan branch if necessary and commits only `data/state.json` to it.

The workflow uses a concurrency group so two runs cannot update the state branch at the same time.
