#!/usr/bin/env bash
# Commits and pushes whatever the backup run changed. The commit message is .gitea/backup-commit-message.txt
# followed by the run report.
# Usage: commit-backup.sh <run report file>
set -euo pipefail

report="$1"
template="$(dirname "$0")/../backup-commit-message.txt"

git add --all --verbose
if git diff --cached --stat --exit-code; then
  echo "Nothing changed, nothing to commit."
  exit 0
fi

message="$(mktemp)"
cat "$template" > "$message"
if [ -f "$report" ]; then
  cat "$report" >> "$message"
else
  echo "(no run report)" >> "$message"
fi

git -c user.name="fusion-backup" -c user.email="fusion-backup@example.com" commit --file "$message"
git push
