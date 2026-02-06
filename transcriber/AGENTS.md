# Agent Instructions — Transcriber Subsystem

This document is for AI agents working on the `transcriber/` subtree of
meeting-notes-processor. Read this before making any changes.

## What This Is

A two-machine audio transcription pipeline:

1. **Laptop** runs `meeting_bar.py` (macOS menu bar app). Detects Zoom/Teams
   meetings, captures audio via VBAN, streams it to the Mac Mini.
2. **pilot** (Mac Mini M1) runs `transcriber.py` (FastAPI server). Receives
   VBAN audio, writes WAV, runs whisper.cpp, POSTs the transcript to
   `meetingnotesd` on nuctu.

```
laptop                          pilot (Mac Mini)                nuctu
┌──────────────────┐     UDP    ┌─────────────────────┐  HTTP   ┌─────────────┐
│ meeting_bar.py   │───VBAN────▶│ transcriber.py      │──POST──▶│meetingnotesd│
│ + vban_send.py   │   :6980   │ + whisper-cli       │  :9876  │             │
└──────────────────┘            └─────────────────────┘         └─────────────┘
```

## Critical Rules

1. **NEVER edit files directly on pilot.** Pilot is deployed from this repo.
   Edit `transcriber/server/transcriber.py` here, commit, then `make deploy`.
2. **Always use `uv run`**, never `python3` or `pip`. All scripts use PEP 723
   inline script metadata for dependencies.
3. **The `server/` directory is the deployment unit.** Only files in `server/`
   get rsynced to pilot. Everything else (meeting_bar.py, vban/, setup/) runs
   on the laptop or is used for provisioning.

## File Map

| File | Runs on | Purpose |
|------|---------|---------|
| `server/transcriber.py` | pilot | FastAPI server: VBAN capture → WAV → whisper → webhook |
| `meeting_bar.py` | laptop | macOS menu bar app, auto meeting detection + recording |
| `meeting.py` | laptop | CLI for manual start/stop/status/devices |
| `vban/vban_send.py` | laptop | VBAN audio sender with optional dual-input mixing |
| `vban/vban_recv.py` | — | **Obsolete.** VBAN capture is now built into transcriber.py |
| `mic_active.swift` | laptop | CoreAudio helper: detects physical mic activity |
| `mic_active` | laptop | Compiled binary of above (git-ignored, `make build`) |
| `Makefile` | laptop | Build, deploy, provision, status, logs |
| `com.transcriber.plist` | pilot | launchd service definition |
| `setup/*.sh` | pilot (via ssh) | Provisioning scripts (homebrew, deps, whisper, service) |
| `SETUP.md` | — | Human setup guide with architecture diagram |

## Development & Deploy Workflow

### Changing the transcription server

```bash
cd transcriber

# 1. Edit the server code
$EDITOR server/transcriber.py

# 2. Commit
git add -A && git commit -m "description"

# 3. Deploy to pilot (rsyncs server/ + restarts launchd service)
make deploy

# 4. Verify
make status   # curl /status on pilot
make logs     # tail -f the service log
```

`make deploy` does:
- `rsync -avz --delete --exclude='recordings/' server/ edd@pilot:~/transcriber/`
- `launchctl bootout` + `launchctl bootstrap` to restart `com.transcriber`

### Changing the menu bar app

`meeting_bar.py` runs locally on the laptop. No deploy step—just restart it:

```bash
uv run meeting_bar.py
```

### Changing mic_active

```bash
make build    # compiles mic_active.swift → mic_active binary
```

### Full provisioning (fresh pilot setup)

```bash
make provision   # runs setup/01-04 scripts remotely via SSH
make model       # downloads whisper model to pilot
make deploy      # deploys server code
```

## Network & Ports

| Port | Protocol | From → To | Purpose |
|------|----------|-----------|---------|
| 6980 | UDP | laptop → pilot | VBAN audio stream |
| 8000 | HTTP | laptop → pilot | Transcriber API (start/stop/status) |
| 9876 | HTTP | pilot → nuctu | Webhook POST of transcript |

Hosts `pilot` and `nuctu` are expected to be resolvable (Tailscale, /etc/hosts,
or mDNS).

## Environment Variables

### transcriber.py (pilot)

| Variable | Default | Notes |
|----------|---------|-------|
| `WHISPER_CLI` | `~/whisper.cpp/build/bin/whisper-cli` | |
| `WHISPER_MODEL` | `~/whisper.cpp/models/ggml-large-v3.bin` | ~3 GB |
| `RECORDINGS_DIR` | `~/transcriber/recordings` | WAV + txt files |
| `WEBHOOK_URL` | `http://nuctu:9876/webhook` | Set in launchd plist |
| `VBAN_PORT` | `6980` | |
| `TRANSCRIBER_HOST` | `0.0.0.0` | |
| `TRANSCRIBER_PORT` | `8000` | |

### meeting_bar.py (laptop)

| Variable | Default | Notes |
|----------|---------|-------|
| `TRANSCRIBER_URL` | `http://pilot:8000` | |
| `PILOT_HOST` | `pilot` | For VBAN target |
| `VBAN_PORT` | `6980` | |
| `MEETING_POLL_INTERVAL` | `5` | Seconds between detection checks |

## Whisper Configuration

- **Model**: `large-v3` (`ggml-large-v3.bin`). Do not downgrade—accuracy matters.
- **Flags**: `-m <model> -f <wav> -l en --print-progress`. Timestamps are
  enabled (no `--no-timestamps` flag).
- **Metal GPU**: whisper.cpp is built with Metal acceleration on the M1.
- **Tinydiarize (`-tdrz`)**: Investigated and rejected. Only has a `small.en`
  model (accuracy regression) and the project appears stalled.

## Meeting Detection — Key Constraints

### Zoom
Simple: check for `CptHost` subprocess via `pgrep -xq CptHost`.

### Teams (complex — read carefully before changing)

Teams 2.x (new Electron) exposes no reliable window titles and
`AVCaptureDevice` cannot see its mic usage. Detection uses two tiers:

- **Start detection**: MSTeams process running AND physical mic has active
  CoreAudio I/O (checked via compiled `mic_active` helper). The Swift helper
  filters virtual devices (BlackHole, ZoomAudioDevice, etc.) to avoid false
  positives.

- **End detection**: Cannot use mic state because our own VBAN sender keeps the
  mic active during recording. Instead queries macOS `audiomxd` system log for
  Teams audio session events.

If you change detection logic, test both start and end transitions for both
Zoom and Teams.

## Threading Model (meeting_bar.py)

The app uses `rumps` (Cocoa NSStatusBar wrapper). Key rules:

1. **Main thread** = Cocoa run loop. All UI updates must happen here.
2. **Poll thread** runs `_poll_loop()` for meeting detection every N seconds.
3. **Recording threads** handle start/stop I/O (VBAN sender, API calls).
4. **Background threads MUST NOT touch Cocoa objects.** Use
   `callAfter(fn, *args)` from `PyObjCTools.AppHelper` to dispatch to the
   main thread. Never use `rumps.Timer.set_callback()` from a background
   thread.
5. State transitions (idle/recording) are guarded by `self._recording_lock`.

## Audio Routing

The VBAN sender (`vban_send.py`) supports two modes:

- **Single device**: Captures from one audio input (e.g., ZoomAudioDevice).
- **Dual-input mixing**: Captures from a primary device (e.g., BlackHole 2ch
  for remote participants) AND a microphone (for local voice), mixes them in
  software. Use `--mic` flag.

Device preference order in meeting_bar.py: BlackHole 2ch → ZoomAudioDevice →
Microsoft Teams.

BlackHole requires a reboot after installation to register as a system audio
device.

## Makefile Reference

| Target | Where | What |
|--------|-------|------|
| `build` | local | Compile `mic_active.swift` → `mic_active` |
| `deploy` | remote | rsync `server/` to pilot + restart service |
| `check` | remote | Full health report (OS, brew, whisper, model, service) |
| `status` | remote | `curl /status` on pilot |
| `logs` | remote | `tail -f` the transcriber log |
| `ssh` | remote | Open shell on pilot |
| `provision` | remote | Run all 4 setup scripts |
| `model` | remote | Download/update whisper model |
| `test` | remote | Round-trip test |
| `clean-vban` | remote | Remove obsolete vban-receiver service |

## Common Pitfalls

- **Editing pilot directly**: `make deploy` does `--delete`, so direct edits
  on pilot will be overwritten. Always edit in this repo.
- **VBAN sender keeps mic alive**: This is why Teams end-detection uses
  `audiomxd` logs instead of mic state. Don't "fix" this by checking mic state
  for end detection.
- **recordings/ directory**: `make deploy` excludes it (`--exclude='recordings/'`).
  Never add recordings to git.
- **Python 3.14 + rumps**: The combination works as of rumps 0.4.0. Earlier
  versions have pyobjc incompatibilities.
- **Cocoa threading**: SEGV on exit or random crashes usually mean a background
  thread touched a Cocoa object. See Threading Model above.
