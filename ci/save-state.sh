#!/usr/bin/env bash
set -euo pipefail

STATE_BRANCH="${STATE_BRANCH:-watcher-state}"
STATE_FILE="${STATE_FILE:-data/state.json}"

: "${GITLAB_STATE_PUSH_TOKEN:?Missing GITLAB_STATE_PUSH_TOKEN}"
: "${CI_SERVER_HOST:?Missing CI_SERVER_HOST}"
: "${CI_PROJECT_PATH:?Missing CI_PROJECT_PATH}"
: "${CI_COMMIT_SHA:?Missing CI_COMMIT_SHA}"

if [[ ! -s "$STATE_FILE" ]]; then
  echo "State file $STATE_FILE is missing or empty." >&2
  exit 1
fi

TEMP_DIR="$(mktemp -d)"
STATE_WORKTREE="$TEMP_DIR/state"

cleanup() {
  git worktree remove --force "$STATE_WORKTREE" >/dev/null 2>&1 || true
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

if git ls-remote --exit-code --heads origin "$STATE_BRANCH" >/dev/null 2>&1; then
  git fetch --quiet --depth=1 origin "$STATE_BRANCH"
  git worktree add --detach "$STATE_WORKTREE" FETCH_HEAD
else
  git worktree add --detach "$STATE_WORKTREE" "$CI_COMMIT_SHA"
  git -C "$STATE_WORKTREE" switch --orphan "$STATE_BRANCH"
  git -C "$STATE_WORKTREE" rm -rf . >/dev/null 2>&1 || true
fi

mkdir -p "$STATE_WORKTREE/$(dirname "$STATE_FILE")"
cp "$STATE_FILE" "$STATE_WORKTREE/$STATE_FILE"

git -C "$STATE_WORKTREE" config user.email "draft-watcher@example.invalid"
git -C "$STATE_WORKTREE" config user.name "Sabatini Draft Watcher"
git -C "$STATE_WORKTREE" add "$STATE_FILE"

if git -C "$STATE_WORKTREE" diff --cached --quiet; then
  echo "No state change to commit."
  exit 0
fi

git -C "$STATE_WORKTREE" commit -m "Update Sabatini draft snapshot [skip ci]"

PUSH_URL="https://oauth2:${GITLAB_STATE_PUSH_TOKEN}@${CI_SERVER_HOST}/${CI_PROJECT_PATH}.git"
git -C "$STATE_WORKTREE" push "$PUSH_URL" "HEAD:refs/heads/$STATE_BRANCH"
echo "Saved $STATE_FILE to $STATE_BRANCH."
