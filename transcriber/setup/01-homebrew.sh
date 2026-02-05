#!/bin/bash
# 01-homebrew.sh — Install Homebrew on pilot (idempotent)
set -euo pipefail

echo "--- Checking Homebrew ---"

if command -v brew &>/dev/null; then
    echo "Homebrew already installed: $(brew --version | head -1)"
    brew update
    echo "Homebrew updated."
    exit 0
fi

# Check for Homebrew in standard M1 location (may exist but not be in PATH)
if [ -x /opt/homebrew/bin/brew ]; then
    echo "Homebrew binary found at /opt/homebrew/bin/brew but not in PATH."
    echo "Adding to shell profile..."
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
    eval "$(/opt/homebrew/bin/brew shellenv)"
    echo "Homebrew ready: $(brew --version | head -1)"
    exit 0
fi

echo "Installing Homebrew..."
NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Add to PATH for future shells
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

echo "Homebrew installed: $(brew --version | head -1)"
