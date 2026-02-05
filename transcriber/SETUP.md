# Transcriber Setup Guide

End-to-end setup for meeting transcription: laptop audio → VBAN streaming → Mac Mini transcription → webhook.

This guide covers both the **client** (your laptop) and the **server** (the transcription appliance, "pilot"), starting from scratch.

## Architecture Overview

```
┌─────────── Your Laptop ─────────────────────────────────────────────┐
│                                                                     │
│  SoundSource:                                                       │
│    Zoom/Teams audio ─┬─► Your speakers (you hear the meeting)       │
│                      └─► BlackHole 2ch (captured for transcription) │
│                                                                     │
│  vban_send.py (launched by meeting.py):                             │
│    BlackHole 2ch ──► ┐                                              │
│    (remote audio)    ├─ mix ─► VBAN UDP packets ─► pilot:6980       │
│    Your mic ───────► ┘                                              │
│    (your voice)                                                     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

                        ▼  VBAN over Tailscale / LAN  ▼

┌─────────── Pilot (Mac Mini M1) ─────────────────────────────────────┐
│                                                                     │
│  transcriber.py (FastAPI on port 8000):                             │
│    UDP :6980 ─► VBANCapture ─► WAV file                             │
│    WAV file ─► whisper.cpp (large-v3, Metal GPU) ─► transcript      │
│    transcript ─► POST to meetingnotesd webhook                      │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

                        ▼  HTTP webhook  ▼

┌─────────── meetingnotesd ───────────────────────────────────────────┐
│  Receives transcript, runs AI summarization, writes org-mode notes  │
└─────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

| Item | Where | Purpose |
|------|-------|---------|
| Mac with Apple Silicon | Laptop | Audio capture and VBAN streaming |
| Mac Mini M1+ | Server ("pilot") | Whisper transcription with Metal GPU |
| [Tailscale](https://tailscale.com/) | Both | Secure networking between machines |
| [BlackHole 2ch](https://existential.audio/blackhole/) | Laptop | Virtual audio device for routing |
| [SoundSource](https://rogueamoeba.com/soundsource/) | Laptop | Per-app audio output routing |
| SSH key access | Laptop → pilot | Deployment and management |

---

## Part 1: Server Setup (Pilot)

The server runs the transcriber service — whisper.cpp for speech-to-text, listening for VBAN audio packets from your laptop.

### 1.1 Initial Provisioning

From your laptop, in this repo:

```bash
cd transcriber

# Check connectivity and system info
make check

# Full provisioning (Homebrew, dependencies, whisper.cpp, launchd service)
make provision
```

This runs four scripts in order:
1. **01-homebrew.sh** — Installs Homebrew on pilot
2. **02-dependencies.sh** — Installs ffmpeg and uv
3. **03-whisper.sh** — Clones, builds whisper.cpp with Metal support, downloads large-v3 model
4. **04-service.sh** — Installs and loads the `com.transcriber` launchd service

### 1.2 Deploy the Transcriber

```bash
make deploy
```

This rsyncs `server/transcriber.py` to `~/transcriber/` on pilot and restarts the service.

### 1.3 Verify

```bash
make status
# Should return: {"status":"ok","service":"transcriber","vban_port":6980,...}

make logs
# Watch for startup messages
```

### 1.4 How It Works

The transcriber is a FastAPI server (`transcriber.py`) running on port 8000:

- **`POST /start`** — Opens a UDP socket on port 6980, starts capturing VBAN packets directly to a WAV file
- **`POST /stop`** — Stops capture, closes the WAV, runs whisper-cli on it, POSTs the transcript to the webhook
- **`GET /status`** — Health check with disk space, current recording state, etc.

The transcriber captures VBAN audio directly — no BlackHole, no ffmpeg, no intermediate services on the server side.

### 1.5 Configuration

Environment variables (set in `com.transcriber.plist`):

| Variable | Default | Description |
|----------|---------|-------------|
| `WEBHOOK_URL` | `http://nuctu:9876/webhook` | Where to POST transcripts |
| `VBAN_PORT` | `6980` | UDP port for VBAN audio |
| `WHISPER_CLI` | `~/whisper.cpp/build/bin/whisper-cli` | Path to whisper binary |
| `WHISPER_MODEL` | `~/whisper.cpp/models/ggml-large-v3.bin` | Whisper model file |
| `RECORDINGS_DIR` | `~/transcriber/recordings` | Where WAV files are stored |
| `TRANSCRIBER_HOST` | `0.0.0.0` | Listen address |
| `TRANSCRIBER_PORT` | `8000` | HTTP API port |

---

## Part 2: Client Setup (Laptop)

The laptop captures meeting audio and streams it to pilot via VBAN.

### 2.1 Install BlackHole 2ch

```bash
brew install --cask blackhole-2ch
```

**Reboot after installation** — the audio driver needs a restart to load.

After rebooting, verify it appears:
```bash
system_profiler SPAudioDataType | grep -i blackhole
```

You should see "BlackHole 2ch" listed. Do **not** set it as your default audio device.

### 2.2 Configure SoundSource

SoundSource routes per-app audio output. We use it to send Zoom/Teams audio to BlackHole while you still hear it through your speakers.

> **Important:** SoundSource routes per-app *output* only. It cannot route microphone input. Your mic is captured separately by the VBAN sender's dual-input mixing mode.

1. **Open SoundSource** (menu bar icon)
2. **Configure Zoom:**
   - Find **zoom.us** in the Applications list (click **+** to add if needed)
   - Click the **Output** dropdown
   - Select **Multi-Output** → check both:
     - ✅ Your normal speakers/headphones
     - ✅ **BlackHole 2ch**
3. **Configure Microsoft Teams:**
   - Same as Zoom: set Output to Multi-Output with speakers + BlackHole 2ch
4. **Optional — Save as Profile:**
   - Save as "Meeting Recording" for quick toggling

After configuration, meeting audio flows to both your ears AND BlackHole simultaneously.

### 2.3 Verify Audio Devices

```bash
cd transcriber
uv run meeting.py devices
```

You should see:
- `BlackHole 2ch` marked as ★ RECOMMENDED
- Your mic (e.g., "Yeti Stereo Microphone", "MacBook Air Microphone") auto-detected for mixing

### 2.4 Test Connectivity

```bash
# Check transcriber is reachable
uv run meeting.py status

# Quick round-trip test: start, speak briefly, stop
uv run meeting.py start "Setup Test"
# ... speak for a few seconds ...
uv run meeting.py stop
```

Check pilot logs to verify transcription completed:
```bash
cd transcriber && make logs
```

---

## Part 3: Daily Usage

Once setup is complete, this is all you need:

```bash
cd transcriber

# Start of meeting:
uv run meeting.py start "Weekly Standup"

# End of meeting:
uv run meeting.py stop

# Check status anytime:
uv run meeting.py status
```

### What Happens Under the Hood

1. `meeting.py start` detects BlackHole + your mic, launches `vban_send.py` in dual-input mixed mode
2. VBAN sender captures from BlackHole (remote participants) AND your mic (your voice), mixes them, streams UDP packets to pilot
3. `meeting.py start` calls `POST /start` on pilot's transcriber, which opens a VBAN capture socket
4. When you run `meeting.py stop`, pilot writes the WAV, runs whisper.cpp, and POSTs the transcript to the meetingnotesd webhook
5. meetingnotesd runs AI summarization and writes org-mode notes

### Command Reference

```bash
uv run meeting.py start "Title"     # Start recording
uv run meeting.py stop              # Stop and transcribe
uv run meeting.py status            # Show sender + transcriber state
uv run meeting.py devices           # List audio devices

# Options:
uv run meeting.py start "Title" -d ZoomAudioDevice   # Use specific input device
uv run meeting.py start "Title" -m "MacBook Air Mic" # Use specific mic
```

---

## Part 4: Makefile Reference

All server management happens from your laptop via `make`:

```bash
cd transcriber

# Daily operations
make status          # Transcriber health check
make logs            # Tail transcriber logs

# Deployment
make deploy          # Rsync server/ to pilot, restart service
make check           # Connectivity + system check

# Provisioning (first-time or rebuilding)
make provision       # Full setup (Homebrew, deps, whisper, service)
make model           # Re-download whisper model

# Cleanup
make clean-vban      # Remove obsolete vban-receiver service from pilot

# Utilities
make ssh             # SSH into pilot
make test            # Quick health check
```

---

## Troubleshooting

### Transcriber not reachable

```bash
make check            # Is pilot reachable at all?
make status           # Is the service running?
make logs             # Check for startup errors
ssh edd@pilot "launchctl list | grep transcriber"
```

### No audio captured (WAV too small)

- Is the VBAN sender running? Check `uv run meeting.py status`
- Is audio being routed? Play a Zoom/Teams test call and check SoundSource meters
- Check sender log: `cat /tmp/meeting-vban-sender.log`
- Verify ports: sender sends to pilot:6980, transcriber listens on 6980

### BlackHole 2ch not appearing

- Did you reboot after `brew install --cask blackhole-2ch`?
- Check: `system_profiler SPAudioDataType | grep -i blackhole`

### Only capturing remote audio (no mic)

- Check mic detection: `uv run meeting.py devices`
- System default input should be your real mic, not a virtual device
- Specify mic manually: `uv run meeting.py start "Test" -m "MacBook Air Mic"`
- Check sender log for "mixed mode": `cat /tmp/meeting-vban-sender.log`

### Only capturing mic (no remote audio)

- Verify SoundSource is routing Zoom/Teams output → BlackHole 2ch
- Check SoundSource shows Multi-Output with BlackHole checked for the app
- Make sure the meeting app is actually running and producing audio

### Transcription quality issues

- whisper.cpp with large-v3 on M1 is generally excellent
- Very short recordings (< 3s) may produce empty output
- Check WAV quality: `ssh edd@pilot "python3 -c \"import wave; w=wave.open('<path>'); print(f'{w.getframerate()}Hz {w.getnframes()/w.getframerate():.1f}s')\""`

### Redeploying after code changes

```bash
cd transcriber
make deploy           # Pushes server/ to pilot and restarts
```

### Rebuilding whisper.cpp

```bash
make provision-whisper   # Rebuilds from latest source
make model               # Re-downloads model
```

---

## Network Configuration

| Service | Host | Port | Protocol | Direction |
|---------|------|------|----------|-----------|
| VBAN audio | pilot | 6980 | UDP | Laptop → pilot |
| Transcriber API | pilot | 8000 | HTTP | Laptop → pilot |
| meetingnotesd | nuctu | 9876 | HTTP | Pilot → nuctu |

The laptop and pilot communicate over Tailscale. Ensure both machines are on the same tailnet and can resolve each other's hostnames.

---

## File Locations

### Laptop

| File | Purpose |
|------|---------|
| `transcriber/meeting.py` | Main user command |
| `transcriber/vban/vban_send.py` | VBAN audio sender |
| `transcriber/Makefile` | Server management |
| `transcriber/server/transcriber.py` | Server code (deployed to pilot) |
| `transcriber/com.transcriber.plist` | launchd service definition |
| `transcriber/setup/` | Provisioning scripts |
| `/tmp/meeting-vban-sender.log` | VBAN sender log |
| `/tmp/meeting-vban-sender.pid` | VBAN sender PID file |

### Pilot (Server)

| File | Purpose |
|------|---------|
| `~/transcriber/transcriber.py` | Running transcriber server |
| `~/transcriber/recordings/` | WAV files and transcripts |
| `~/Library/Logs/transcriber.log` | Service log |
| `~/Library/LaunchAgents/com.transcriber.plist` | launchd plist |
| `~/whisper.cpp/` | whisper.cpp source and build |
| `~/whisper.cpp/models/ggml-large-v3.bin` | Whisper model (~3GB) |
