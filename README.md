# Sabatini Python Draft Watcher

A small, readable GitLab scheduled-pipeline version. It renders Sabatini's JavaScript-powered Untappd embed with Playwright, parses the menu with Beautiful Soup, compares it with `data/state.json`, and posts a `diff` code block to Discord when the draft list changes.

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

These settings affect only the normalized `lines` used for Discord messages. Every parsed field is still stored under `items` in `data/state.json`. If a hidden field changes, the pipeline updates and commits the full JSON snapshot without posting anything to Discord.

GitLab CI/CD variables:

- `DISCORD_WEBHOOK_URL`
- `GITLAB_STATE_PUSH_TOKEN`
- optional `UNTAPPD_EMBED_URL`

Run from GitLab with the same hourly schedule, for example:

```cron
17 * * * *
```

The first run always posts the full menu and saves it to `data/state.json`. Every later run posts only removed and added beer lines, with no unchanged menu items included. A missing, blank, empty, or manually deleted state file counts as a first run, even when the `watcher-state` branch already exists.

The website's `Updated` timestamp is also part of the Discord comparison. If Untappd changes only that timestamp, Discord receives a small diff containing the old and new timestamps even when every beer is unchanged.

Discord messages include the menu title and current website timestamp, but omit the fixed source URL. The URL remains stored in `data/state.json`.

For consistent Discord mobile highlighting, internal ASCII hyphens are displayed as en dashes. The real leading `- ` on a removed beer remains an ASCII diff marker, so genuine removals still color the full line red. This formatting happens only in Discord output; JSON retains the original Untappd text.

Webhook requests use Discord's required `DiscordBot (URL, version)` User-Agent format. In GitLab, the URL comes from the predefined `CI_PROJECT_URL` variable; this avoids Cloudflare error 1010 blocking Python's default `urllib` signature.

## Separate state branch

Code lives on `main`. Snapshot commits live on the orphan `watcher-state` branch, which is used as storage and is never merged into `main`.

The CI helpers handle this automatically:

1. `ci/load-state.sh` loads `data/state.json` from `watcher-state` before the watcher runs.
2. `watch.py` checks the current menu and updates the local state when needed.
3. `ci/save-state.sh` creates the orphan branch if necessary and commits only `data/state.json` to it.

The `resource_group` setting prevents two scheduled jobs from updating the state branch at the same time.

### Migrating an existing repository

1. Push these updated files to `main`, but leave any currently tracked `data/state.json` in place temporarily.
2. Confirm `GITLAB_STATE_PUSH_TOKEN` still has `write_repository` permission and can create/push `watcher-state`.
3. Run the scheduled pipeline manually once. It uses the existing state as its baseline and creates the orphan branch without reposting the full menu unnecessarily.
4. In GitLab, open **Code > Branches** and verify that `watcher-state` contains only `data/state.json`.
5. Remove the old snapshot from `main`, commit that deletion, and push. It stays out because `.gitignore` now excludes it.

For step 5 from a local clone:

```bash
git rm --cached data/state.json
git commit -m "Move draft state to watcher-state branch"
git push
```

If `data/state.json` was not already tracked on `main`, skip the migration-specific removal. The first scheduled run creates `watcher-state` and posts the full initial menu.
