#!/bin/bash
# 02-dependencies.sh — Install ffmpeg and uv on pilot (idempotent)
#
# Note: BlackHole 2ch is no longer required on pilot — the transcriber
# captures VBAN audio packets directly via UDP, bypassing BlackHole and ffmpeg
# for recording. ffmpeg is kept as a general-purpose audio utility.
set -euo pipefail

# Ensure brew is in PATH
eval "$(/opt/homebrew/bin/brew shellenv)"

echo "--- Installing dependencies ---"

# ffmpeg (general-purpose audio/video tool)
if brew list ffmpeg &>/dev/null; then
    echo "ffmpeg already installed: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "Installing ffmpeg..."
    brew install ffmpeg
    echo "ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
fi

# uv (Python package manager)
if command -v uv &>/dev/null; then
    echo "uv already installed: $(uv --version)"
else
    echo "Installing uv..."
    brew install uv
    echo "uv installed: $(uv --version)"
fi

echo "--- All dependencies installed ---"
brew list --formula | grep -E 'ffmpeg|uv' || true
