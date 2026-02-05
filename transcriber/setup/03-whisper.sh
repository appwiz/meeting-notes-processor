#!/bin/bash
# 03-whisper.sh — Clone, build, and set up whisper.cpp on pilot (idempotent)
set -euo pipefail

# Ensure brew is in PATH
eval "$(/opt/homebrew/bin/brew shellenv)"

WHISPER_DIR="$HOME/whisper.cpp"
MODEL="large-v3"

echo "--- Setting up whisper.cpp ---"

# Install cmake if not present (needed for building)
if ! command -v cmake &>/dev/null; then
    echo "Installing cmake..."
    brew install cmake
else
    echo "cmake already installed: $(cmake --version | head -1)"
fi

# Clone or update whisper.cpp
if [ -d "$WHISPER_DIR" ]; then
    echo "whisper.cpp already cloned, pulling latest..."
    cd "$WHISPER_DIR"
    git pull --ff-only || echo "Pull failed (maybe on a tag), continuing with current version"
else
    echo "Cloning whisper.cpp..."
    git clone https://github.com/ggml-org/whisper.cpp.git "$WHISPER_DIR"
    cd "$WHISPER_DIR"
fi

# Build with cmake (Metal is auto-detected on Apple Silicon)
echo "Building whisper.cpp..."
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j --config Release

# Verify the binary was built
if [ -x build/bin/whisper-cli ]; then
    echo "whisper-cli built successfully"
    ./build/bin/whisper-cli --version 2>&1 || true
else
    echo "ERROR: whisper-cli binary not found!"
    exit 1
fi

# Download the ggml model
if [ -f "models/ggml-${MODEL}.bin" ]; then
    echo "Model ggml-${MODEL}.bin already downloaded"
else
    echo "Downloading ${MODEL} model (this may take a while)..."
    bash ./models/download-ggml-model.sh ${MODEL}
fi

echo "--- whisper.cpp setup complete ---"
echo "Binary: $WHISPER_DIR/build/bin/whisper-cli"
echo "Model:  $WHISPER_DIR/models/ggml-${MODEL}.bin"
ls -lh "$WHISPER_DIR/models/ggml-${MODEL}.bin" 2>/dev/null || echo "Model file not found!"
