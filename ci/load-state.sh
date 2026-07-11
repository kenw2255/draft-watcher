#!/usr/bin/env bash
set -euo pipefail

STATE_BRANCH="${STATE_BRANCH:-watcher-state}"
STATE_FILE="${STATE_FILE:-data/state.json}"

mkdir -p "$(dirname "$STATE_FILE")"

if git ls-remote --exit-code --heads origin "$STATE_BRANCH" >/dev/null 2>&1; then
  git fetch --quiet --depth=1 origin "$STATE_BRANCH"

  if git cat-file -e "FETCH_HEAD:$STATE_FILE" 2>/dev/null; then
    git show "FETCH_HEAD:$STATE_FILE" > "$STATE_FILE"
    echo "Loaded $STATE_FILE from $STATE_BRANCH."
  else
    rm -f "$STATE_FILE"
    echo "$STATE_BRANCH exists, but $STATE_FILE is missing. This is a fresh run."
  fi
elif [[ -s "$STATE_FILE" ]]; then
  echo "No $STATE_BRANCH branch yet; using the existing state file from main."
else
  rm -f "$STATE_FILE"
  echo "No previous state found. This run will post the full menu."
fi
