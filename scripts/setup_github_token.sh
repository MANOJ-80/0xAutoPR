#!/bin/bash
# Safely write GitHub token from gh CLI into .env (run locally, never share output)
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v gh &>/dev/null; then
  echo "Install GitHub CLI: https://cli.github.com/"
  exit 1
fi

TOKEN=$(gh auth token)
if grep -q '^GITHUB_TOKEN=' .env 2>/dev/null; then
  sed -i "s|^GITHUB_TOKEN=.*|GITHUB_TOKEN=${TOKEN}|" .env
else
  echo "GITHUB_TOKEN=${TOKEN}" >> .env
fi
echo "GITHUB_TOKEN updated in .env"
