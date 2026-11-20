#!/usr/bin/env bash
# Physiology Analysis Tools - starts the app (macOS / Linux). Double-click
# this file, or run "bash start-app.command" in Terminal.

cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ ! -x .venv/bin/python ] || [ ! -d node_modules ]; then
  echo 'The app is not installed yet. Run "bash installation/install.sh" in this folder first.'
  read -r -p 'Press Enter to close.'
  exit 1
fi

echo 'Starting Physiology Analysis Tools...'
echo 'Keep this window open while you use the app. The app window opens in a moment.'
echo
npm run dev
