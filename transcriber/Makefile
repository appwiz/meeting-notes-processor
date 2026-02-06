# Transcriber Appliance — local build + remote management via SSH
#
# Local targets: build (compiles mic_active Swift helper)
# Remote targets: all others operate on the Mac Mini "pilot" over SSH
# Usage: cd transcriber && make <target>

REMOTE_HOST := edd@pilot
REMOTE_DIR := ~/transcriber
WHISPER_DIR := ~/whisper.cpp
WHISPER_MODEL := large-v3
SERVICE_LABEL := com.transcriber
PLIST := $(SERVICE_LABEL).plist

.PHONY: check provision deploy logs status ssh test model build

# ---------------------------------------------------------------------------
# Local Build
# ---------------------------------------------------------------------------

build: mic_active  ## Build all local binaries

mic_active: mic_active.swift
	swiftc -O -o $@ $<
	@echo "Built mic_active (CoreAudio physical-mic detector)"

# ---------------------------------------------------------------------------
# Connectivity & Status
# ---------------------------------------------------------------------------

check:
	@echo "=== Checking pilot connectivity ==="
	@ssh -o ConnectTimeout=5 $(REMOTE_HOST) '\
		eval "$$(/opt/homebrew/bin/brew shellenv 2>/dev/null)" 2>/dev/null; \
		echo "Hostname:  $$(hostname)"; \
		echo "Arch:      $$(uname -m)"; \
		echo "macOS:     $$(sw_vers -productVersion)"; \
		echo "CPU:       $$(sysctl -n machdep.cpu.brand_string)"; \
		echo "Memory:    $$(sysctl -n hw.memsize | awk "{print \$$1/1073741824 \" GB\"}")"; \
		echo "Disk free: $$(df -h / | tail -1 | awk "{print \$$4}")"; \
		echo "---"; \
		echo "Homebrew:  $$(brew --version 2>/dev/null | head -1 || echo "not installed")"; \
		echo "ffmpeg:    $$(ffmpeg -version 2>/dev/null | head -1 || echo "not installed (optional)")"; \
		echo "uv:        $$(uv --version 2>/dev/null || echo "not installed")"; \
		echo "whisper:   $$(test -x $(WHISPER_DIR)/build/bin/whisper-cli && echo "compiled" || echo "not built")"; \
		echo "model:     $$(test -f $(WHISPER_DIR)/models/ggml-$(WHISPER_MODEL).bin && echo "$(WHISPER_MODEL) downloaded" || echo "not downloaded")"; \
		echo "service:   $$(launchctl list 2>/dev/null | grep -q $(SERVICE_LABEL) && echo "loaded" || echo "not loaded")"; \
		echo "vban-recv: $$(launchctl list 2>/dev/null | grep -q com.vban-receiver && echo "LOADED (obsolete!)" || echo "not loaded (good)")"; \
		echo "---"; \
		echo "Transcriber dir: $$(test -d $(REMOTE_DIR) && echo "exists" || echo "not deployed")"; \
	'
	@echo "=== pilot reachable ==="

ssh:
	ssh $(REMOTE_HOST)

status:
	@ssh $(REMOTE_HOST) 'curl -s http://localhost:8000/status 2>/dev/null || echo "Service not running"'

logs:
	@ssh $(REMOTE_HOST) 'tail -f ~/Library/Logs/transcriber.log 2>/dev/null || echo "No log file found"'

# ---------------------------------------------------------------------------
# Provisioning (run setup scripts remotely, in order)
# ---------------------------------------------------------------------------

provision: provision-homebrew provision-deps provision-whisper provision-service
	@echo "=== Provisioning complete ==="

provision-homebrew:
	@echo "=== [1/4] Homebrew ==="
	@ssh $(REMOTE_HOST) 'bash -s' < setup/01-homebrew.sh

provision-deps:
	@echo "=== [2/4] Dependencies ==="
	@ssh $(REMOTE_HOST) 'bash -s' < setup/02-dependencies.sh

provision-whisper:
	@echo "=== [3/4] whisper.cpp + CoreML ==="
	@ssh $(REMOTE_HOST) 'bash -s' < setup/03-whisper.sh

provision-service:
	@echo "=== [4/4] launchd service ==="
	@scp $(PLIST) $(REMOTE_HOST):~/Library/LaunchAgents/$(PLIST)
	@ssh $(REMOTE_HOST) 'bash -s' < setup/04-service.sh

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

deploy:
	@echo "=== Deploying server to pilot ==="
	rsync -avz --delete --exclude='recordings/' server/ $(REMOTE_HOST):$(REMOTE_DIR)/
	@echo "=== Restarting service ==="
	@ssh $(REMOTE_HOST) '\
		launchctl bootout gui/$$(id -u) ~/Library/LaunchAgents/$(PLIST) 2>/dev/null; \
		sleep 1; \
		launchctl bootstrap gui/$$(id -u) ~/Library/LaunchAgents/$(PLIST); \
		sleep 2; \
		curl -s http://localhost:8000/status || echo "Warning: service may not have started"; \
	'
	@echo "=== Deploy complete ==="

# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------

model:
	@echo "=== Downloading/updating whisper model ($(WHISPER_MODEL)) ==="
	@ssh $(REMOTE_HOST) 'cd $(WHISPER_DIR) && bash models/download-ggml-model.sh $(WHISPER_MODEL)'

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test:
	@echo "=== Running round-trip test ==="
	@ssh $(REMOTE_HOST) 'curl -s http://localhost:8000/status'

# ---------------------------------------------------------------------------
# Cleanup — remove obsolete vban-receiver service from pilot
# (VBAN capture is now built into the transcriber directly)
# ---------------------------------------------------------------------------

clean-vban:
	@echo "=== Removing obsolete VBAN receiver from pilot ==="
	@ssh $(REMOTE_HOST) '\
		launchctl bootout gui/$$(id -u) ~/Library/LaunchAgents/com.vban-receiver.plist 2>/dev/null && echo "Service unloaded" || echo "Service not loaded"; \
		rm -f ~/Library/LaunchAgents/com.vban-receiver.plist && echo "Plist removed"; \
		rm -f ~/transcriber/vban_recv.py && echo "vban_recv.py removed"; \
	'
	@echo "=== Cleanup complete ==="
