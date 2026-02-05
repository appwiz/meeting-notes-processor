#!/bin/bash
# 02-dependencies.sh — Install ffmpeg, BlackHole, and uv on pilot (idempotent)
set -euo pipefail

# Ensure brew is in PATH
eval "$(/opt/homebrew/bin/brew shellenv)"

echo "--- Installing dependencies ---"

# ffmpeg (needed for audio conversion)
if brew list ffmpeg &>/dev/null; then
    echo "ffmpeg already installed: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "Installing ffmpeg..."
    brew install ffmpeg
    echo "ffmpeg installed: $(ffmpeg -version 2>&1 | head -1)"
fi

# BlackHole 2ch (virtual audio device for routing)
if brew list --cask blackhole-2ch &>/dev/null; then
    echo "BlackHole 2ch already installed"
else
    echo "Installing BlackHole 2ch..."
    brew install --cask blackhole-2ch
    echo "BlackHole 2ch installed"
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
brew list --cask | grep blackhole || true
