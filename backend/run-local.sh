#!/bin/bash
# Starts the backend locally, reading provider keys from the macOS Keychain (never from a file).
# Store a key once:  security add-generic-password -U -s meeting-copilot -a GROQ_API_KEY -w
# (the -w with no value prompts for it, so it never lands in shell history)
set -euo pipefail
cd "$(dirname "$0")"

for name in GROQ_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY DEEPGRAM_API_KEY TAVILY_API_KEY BRAVE_API_KEY; do
  if value=$(security find-generic-password -s meeting-copilot -a "$name" -w 2>/dev/null); then
    export "$name=$value"
    echo "loaded $name from Keychain"
  fi
done

exec ../.venv/bin/python -m app.main
