# Transcription Workflow Optimization Plan

**Date:** January 27, 2026 (updated February 5, 2026)  
**Status:** Phases 1-3 complete. Phase 4 (speaker turn detection) decided: timestamps-only approach deployed.

## Problem Statement

Current workflow uses MacWhisper for local transcription with webhook integration. Two issues:

1. **No speaker identification** when webhook integration is in use
2. **High CPU load** during transcription makes Zoom/Teams unusable for back-to-back meetings

**Constraint:** Uses both Zoom and Teams—solution must work with both platforms.  
**Update (Feb 2026):** `taskpolicy` does not work for MacWhisper — CPU throttling is not a viable path. We need to offload recording and transcription entirely.

## Key Insight

**Calendar context makes explicit diarization less critical.** The notes processor already does a good job of speaker identification by cross-referencing transcript content with calendar participants—even without speaker labels in the transcript. This means:

- **Speaker turn detection** (whisper.cpp `-tdrz`) is likely sufficient; full pyannote diarization is overkill
- **Timing metadata is the key unlock**: if we preserve meeting start/end times, processing latency becomes irrelevant

**Implication:** The real priority is capturing recording timestamps, not speeding up transcription or adding diarization.

## Goals

- ~~Get speaker diarization in transcripts~~ → Speaker turn markers + calendar context is sufficient
- ~~Eliminate CPU contention during meetings~~ → Defer processing entirely; timing metadata removes urgency
- Preserve meeting start/end timestamps through the pipeline
- Minimize manual intervention

---

## ~~Option 1: CPU Throttling with `taskpolicy`~~ — RULED OUT

**Status:** ❌ Tested and does not work for MacWhisper. The app ignores background QoS clamping and continues to consume full CPU. This is not a viable path.

Original idea was to use `taskpolicy -c background` to deprioritize MacWhisper's CPU usage. In practice, MacWhisper's transcription threads are not constrained by the background QoS policy.

---

## Option 2: Deferred Processing with Watch Folder

MacWhisper Pro has a **Watch Folder** feature:

1. Record audio during meetings (minimal CPU via system audio capture)
2. Have MacWhisper watch a folder for files to transcribe
3. Processing happens automatically during breaks

**Key requirement:** Capture recording start/end times when saving audio files so they can be embedded in the transcript header.

### Automation Script Idea

```bash
#!/bin/bash
# Wait until both Zoom and Teams calls have ended before processing

is_in_call() {
    # Check Zoom
    if pgrep -q "zoom.us" && lsof -c zoom.us 2>/dev/null | grep -q "UDP"; then
        return 0
    fi
    # Check Teams
    if pgrep -q "Teams" && lsof -c Teams 2>/dev/null | grep -q "UDP"; then
        return 0
    fi
    return 1
}

while is_in_call; do
    sleep 60
done

# Trigger transcription - timing is irrelevant since metadata is preserved
```

### Pros
- No CPU impact during meetings
- Keeps everything local
- Fully automatable
- Processing latency doesn't matter if timestamps are preserved

### Cons
- Requires capturing start/end timestamps separately
- More complex setup

### Experiment
- [ ] Test Watch Folder feature in MacWhisper
- [ ] Build script to capture recording timestamps and embed in transcript header
- [ ] Integrate with existing webhook workflow

---

## Option 3: The Transcriber — Dedicated M1 Mac Mini Appliance ⭐ RECOMMENDED

Offload recording and transcription entirely to a dedicated **M1 Mac Mini** connected via **Tailscale**. The laptop only routes audio — zero CPU impact. This supersedes the original "DIY Pipeline" and "Extended Daemon" options by solving both the CPU and timing problems at the hardware level.

### Design Rationale

| Component | Choice | Rationale |
| :--- | :--- | :--- |
| **Compute** | **M1 Mac Mini** | Apple Neural Engine provides elite performance-per-watt for Whisper; near-silent, high-speed transcription |
| **Network** | **Tailscale** | Secure mesh VPN — Mini is reachable via stable IP from anywhere (home, office, tethered) |
| **Audio Routing** | **BlackHole 2ch + dual-input mixing** | Virtual audio device captures app audio; `vban_send.py --mic` mixes local mic in software |
| **Audio Transport** | **VBAN (UDP)** | Low-latency audio streaming optimized for real-time; avoids VPN-induced stutter |
| **Transcription** | **whisper.cpp + CoreML** | C++ port of Whisper with CoreML support; ~10x real-time on M1 |

### Architecture

```
┌─────────────────────────┐         VBAN/UDP          ┌──────────────────────────┐
│    LAPTOP (Director)    │ ◄──── Tailscale ────────► │   MAC MINI (Appliance)   │
│                         │       Audio Stream         │                         │
│  meeting_bar.py detects │                           │  FastAPI server          │
│  Zoom/Teams meetings    │                           │  (transcriber.py)        │
│  vban_send.py streams   │                           │                         │
│  audio (BlackHole +mic) ┼───────────────────────────┼─► VBAN capture → .wav   │
│                         │   POST /start, /stop       │  whisper.cpp transcribes│
│  Auto start/stop ───────┼───────────────────────────┼─► queues & processes     │
│                         │                           │                         │
│                         │      POST /webhook         │  Delivers transcript ───┼──► meetingnotesd.py
│                         │ ◄─────────────────────────┼──with YAML front matter │
└─────────────────────────┘                           └──────────────────────────┘
```

**Laptop (meeting_bar.py — macOS menu bar app):**
- Detects Zoom meetings (pgrep CptHost) and Teams meetings (CoreAudio mic + audiomxd logs)
- Starts/stops VBAN audio stream and transcriber API automatically
- Dual-input mixing: captures BlackHole 2ch (app audio) + physical mic via `vban_send.py --mic`
- Menu bar icon shows recording state (🎙 idle, 🔴 recording, ⚠️ error)

**Mac Mini (transcriber.py — FastAPI server):**
- Captures VBAN audio directly to WAV (built-in VBANCapture class, no ffmpeg or separate receiver)
- On stop: queues audio for whisper.cpp transcription (background task)
- On completion: POSTs transcript with YAML front matter to `meetingnotesd.py` webhook on nuctu

### Implementation Phases

**Phase A: Appliance Setup (Mac Mini)** ✅
- [x] Install Homebrew, Python 3.11+, FFmpeg, uv
- [x] Compile whisper.cpp with Metal acceleration (large-v3 model, 2.9GB)
- [x] Install BlackHole 2ch as virtual audio bridge for VBAN → FFmpeg
- [x] Build FastAPI server with `/start`, `/stop`, `/status`, `/recordings` endpoints
- [x] Background task queue for non-blocking transcription
- [x] launchd service for auto-start (com.transcriber)

**Phase B: Audio Routing (Laptop)** ✅
- [x] VBAN sender/receiver implemented in Python (sounddevice + numpy)
- [x] VBAN receiver absorbed into transcriber.py (VBANCapture class captures UDP directly to WAV — no separate receiver or ffmpeg needed)
- [x] Obsolete `com.vban-receiver` service removed from pilot (`make clean-vban`)
- [x] Audio routing via BlackHole 2ch + dual-input mixing in `vban_send.py --mic` (replaced SoundSource)
- [x] Tested with actual Zoom and Teams meeting audio

**Phase C: Call Detection & Automation (Laptop)** ✅
- [x] `meeting_bar.py` — macOS menu bar app using rumps, auto-detects Zoom and Teams meetings
- [x] Auto-trigger `/start` on meeting detection, `/stop` on meeting end
- [x] Auto-start VBAN sender when meeting detected, stop when meeting ends
- [x] Menu bar icon shows recording state (🎙/🔴/⚠️)
- [x] Manual start/stop via menu bar click
- [x] `mic_active.swift` — CoreAudio helper for physical mic detection (Teams start)
- [x] audiomxd log queries for Teams end detection (VBAN sender keeps mic active)

**Phase D: Transcript Delivery** ✅
- [x] Transcriber POSTs completed transcript to `meetingnotesd.py` webhook on nuctu
- [x] Includes YAML front matter with `meeting_start`, `meeting_end`, `recording_source: transcriber`
- [x] Existing pipeline handles the rest (calendar matching, summarization)

### Speaker Turn Detection — Decision Made

**Approach chosen: timestamps only.** Whisper timestamps are now enabled (removed `--no-timestamps` flag from transcriber.py). The LLM prompt (`prompt.txt`) conditionally uses timestamps to infer speaker turns when present, and falls back to conversational context for legacy transcripts without timestamps.

**Tinydiarize (`-tdrz`) was rejected:** Only has a `small.en` model available (accuracy downgrade from large-v3), and the project appears stalled. Pyannote is overkill. Calendar context + timestamps is sufficient for speaker identification.

### Success Criteria
- **Zero laptop heat:** No CPU spike during transcription
- **Concurrent processing:** Record Meeting 2 while Meeting 1 is still being transcribed
- **Remote functional:** Audio streaming and API calls work over 4G/5G Tailscale connection
- **Timestamps preserved:** Every transcript arrives with `meeting_start`/`meeting_end` in YAML front matter

### Pros
- Completely eliminates CPU load on laptop
- Naturally captures precise meeting timestamps (Director knows when meetings start/end)
- Concurrent recording + transcription
- Works remotely via Tailscale
- Full control over pipeline
- Privacy preserved (all local)
- Integrates cleanly with existing meetingnotesd.py webhook

### Cons
- Requires dedicated hardware (M1 Mac Mini)
- More complex initial setup
- Depends on Tailscale connectivity
- VBAN over cellular may have quality issues (needs testing)

---

## Comparison Matrix

| Option | Preserves Timing | Speaker Turns | CPU Impact | Complexity | Status |
|--------|-----------------|---------------|------------|------------|--------|
| 1. taskpolicy | ❌ | ❌ | ⚠️ Doesn't work | Low | ❌ Ruled out |
| 2. Watch Folder | ✅ (with work) | ❌ | ✅ Deferred | Medium | Superseded by Option 3 |
| 3. Transcriber Appliance | ✅ (native) | ✅ (timestamps) | ✅ Zero | Medium-High | ✅ Implemented |

---

## Recommended Phased Approach

### Phase 1: Timing Metadata in Processor ✅ COMPLETE
Make the processing pipeline timestamp-aware. This is prerequisite for everything else and delivers value immediately (even with MacWhisper, we can inject receipt-time estimates).

- [x] Add YAML front matter support to `meetingnotesd.py` webhook handler (commit 056c286)
- [x] Add `parse_transcript_header()` to `run_summarization.py` (commit 098f091)
- [x] Update calendar matching to use time overlap when timestamps available
- [x] Re-add time context to `build_calendar_aware_prompt()` (with real timestamps, not mtime heuristics)
- [x] Add `pyyaml` to inline script dependencies
- [x] Tests for header parsing and time-based calendar filtering (46 + 28 = 74 tests total)

### Phase 2: Build the Transcriber Appliance ✅ COMPLETE
Set up the M1 Mac Mini as a dedicated transcription server.

- [x] Appliance setup: whisper.cpp compiled with Metal, large-v3 model (commit 62a7d2a)
- [x] FastAPI server with `/start`, `/stop`, `/status`, `/recordings` endpoints
- [x] ffmpeg recording from BlackHole 2ch virtual audio device
- [x] Transcript delivery with YAML front matter to meetingnotesd.py
- [x] launchd service (`com.transcriber`) auto-starts on boot

### Phase 3: Laptop Audio Routing & Call Detection ✅ COMPLETE
Automate the laptop side.

- [x] VBAN audio streaming: `vban_send.py` (laptop) → transcriber.py VBAN capture (pilot)
- [x] VBAN receiver absorbed into transcriber.py; obsolete `com.vban-receiver` removed
- [x] End-to-end verified: laptop → VBAN → pilot → whisper → webhook
- [x] BlackHole 2ch + dual-input mixing replaces SoundSource (app audio + mic in software)
- [x] `meeting_bar.py` menu bar app: auto-detects Zoom/Teams, starts/stops recording
- [x] `mic_active.swift` CoreAudio helper for Teams detection
- [x] Auto-trigger `/start` and `/stop` on the Mini

### Phase 4: Speaker Turn Detection ✅ DECIDED
Timestamps-only approach chosen and deployed.

- [x] Evaluated tinydiarize — rejected (only small.en model, project stalled)
- [x] Evaluated pyannote — rejected (overkill, heavy PyTorch dependency)
- [x] Enabled whisper timestamps (removed `--no-timestamps` from transcriber.py)
- [x] Updated `prompt.txt` to use timestamps for speaker turn inference when present
- [x] Falls back gracefully for legacy transcripts without timestamps

---

## Implementation Strategy

All Transcriber appliance code lives in this repo under `transcriber/`. The Mac Mini ("pilot") is provisioned and deployed to entirely from the laptop via SSH. No manual setup required on pilot itself.

### Target Machine

- **Hostname:** `pilot` (reachable via `ssh edd@pilot`)
- **Hardware:** M1 Mac Mini, 8 GB RAM, arm64
- **OS:** macOS 26.2
- **Current state:** Fully provisioned — Homebrew, uv, whisper.cpp with Metal, large-v3 model. Sleep disabled.

### Repo Layout

```
transcriber/
├── AGENTS.md                 # AI agent instructions for this subsystem
├── Makefile                  # Local build + remote ops via SSH (provision, deploy, logs, etc.)
├── com.transcriber.plist     # launchd service definition (auto-start on boot)
├── meeting_bar.py            # macOS menu bar app — auto meeting detection + recording
├── meeting.py                # CLI for manual start/stop/status/devices
├── mic_active.swift          # CoreAudio physical mic detector (Teams detection)
├── server/
│   └── transcriber.py        # FastAPI server with built-in VBAN capture + whisper (PEP 723 inline deps)
├── setup/
│   ├── 01-homebrew.sh        # Install Homebrew (idempotent)
│   ├── 02-dependencies.sh    # brew install ffmpeg blackhole-2ch; install uv
│   ├── 03-whisper.sh         # Clone & compile whisper.cpp with Metal; download large-v3 model
│   └── 04-service.sh         # Install launchd plist, start service
└── vban/
    ├── vban_send.py           # VBAN sender — runs on laptop, streams audio to pilot
    ├── vban_recv.py           # OBSOLETE — VBAN capture now built into transcriber.py
    └── com.vban-receiver.plist # OBSOLETE — removed from pilot via make clean-vban
```

### Makefile Targets

All targets run from the laptop against pilot via SSH:

| Target | Description |
|--------|-------------|
| `make build` | Compile `mic_active.swift` → `mic_active` binary (local) |
| `make check` | Verify SSH connectivity, show pilot hardware/software status |
| `make provision` | Run all `setup/*.sh` scripts in order (idempotent, safe to re-run) |
| `make deploy` | `rsync` server code to `~/transcriber/` on pilot, restart launchd service |
| `make logs` | Tail the transcriber service logs on pilot |
| `make status` | Show service status + any active recordings |
| `make ssh` | Open an interactive shell on pilot |
| `make test` | Send a test audio clip and verify the full round-trip |
| `make model` | Download/update the whisper.cpp model on pilot |
| `make clean-vban` | Remove obsolete VBAN receiver service from pilot |

### Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Server** | FastAPI via `uv run` | PEP 723 inline deps, consistent with rest of project |
| **Python** | System python3 + `uv` | Avoid Homebrew python; `uv` handles venvs transparently |
| **Provisioning** | Numbered shell scripts over SSH | Idempotent, simple, no Ansible dependency |
| **Service manager** | launchd plist | Native macOS, auto-start on boot, log management |
| **Deployment** | `rsync` + `launchctl restart` | Fast, no build step, atomic |
| **Whisper model** | large-v3 + Metal | Best accuracy; ~3-4x real-time is fine since transcription is async |
| **Audio capture** | Built-in VBAN → WAV in transcriber.py | `VBANCapture` class captures UDP directly to WAV; no ffmpeg or separate receiver needed |
| **Audio transport** | VBAN over UDP (Python) | Custom `vban_send.py` using `sounddevice` + `numpy`; ~256 samples/packet at 48kHz mono int16; supports dual-input mixing |
| **Meeting detection** | `meeting_bar.py` (rumps) | macOS menu bar app; Zoom via pgrep, Teams via CoreAudio + audiomxd |
| **Mic detection** | `mic_active.swift` | Compiled CoreAudio helper, filters virtual devices |

### Provisioning Flow

Each script is idempotent and runs remotely via `ssh edd@pilot 'bash -s' < setup/NN-name.sh`:

1. **01-homebrew.sh** — Install Homebrew if not present, update PATH
2. **02-dependencies.sh** — `brew install ffmpeg blackhole-2ch`; install `uv` via official installer
3. **03-whisper.sh** — Clone whisper.cpp to `~/whisper.cpp`, compile with `WHISPER_COREML=1 make -j`, download large-v3 model, generate CoreML model
4. **04-service.sh** — Copy `com.transcriber.plist` to `~/Library/LaunchAgents/`, load and start the service

### Deployment Flow

```
laptop                              pilot (Mac Mini)
  make deploy
    rsync transcriber/server/ ──────► ~/transcriber/
    ssh: launchctl kickstart ───────► service restarts with new code
    ssh: curl /status ──────────────► verify healthy
```

### Server API (transcriber.py on pilot)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET | Health check: service status, active recordings, disk space |
| `/start` | POST | Begin recording: starts VBANCapture (UDP → WAV); records `meeting_start` timestamp. Body: `{"title": "Meeting Name"}` |
| `/stop` | POST | Stop recording: stops VBAN capture, queues audio for whisper.cpp transcription. Records `meeting_end` timestamp |
| `/recordings` | GET | List recent recordings and their processing status |

On transcription completion, the server POSTs the result to `meetingnotesd.py`:

```json
{
  "title": "Meeting Name",
  "transcript": "---\nmeeting_start: 2026-02-05T14:00:00-08:00\nmeeting_end: 2026-02-05T15:03:00-08:00\nrecording_source: transcriber\n---\n\n[transcript text...]"
}
```

The YAML front matter is embedded in the transcript body. `meetingnotesd.py` saves it as-is to inbox — the processor pipeline picks up the timestamps via `parse_transcript_header()`.

### What Runs Where

| Component | Lives in repo | Deployed to | Runs on |
|-----------|--------------|-------------|---------|
| FastAPI server | `transcriber/server/` | `~/transcriber/` on pilot | pilot |
| whisper.cpp + models | compiled on pilot via setup scripts | `~/whisper.cpp/` on pilot | pilot |
| VBAN sender | `transcriber/vban/vban_send.py` | stays on laptop | laptop |
| Meeting bar app | `transcriber/meeting_bar.py` | stays on laptop | laptop |
| Meeting CLI | `transcriber/meeting.py` | stays on laptop | laptop |
| Mic detector | `transcriber/mic_active.swift` | compiled locally (`make build`) | laptop |
| Setup scripts | `transcriber/setup/` | run via SSH, not deployed | pilot (via laptop) |
| Makefile | `transcriber/Makefile` | stays on laptop | laptop |
| Transcriber plist | `transcriber/com.transcriber.plist` | `~/Library/LaunchAgents/` on pilot | pilot |
| meetingnotesd.py | repo root | already running on nuctu | nuctu |

---

## Transcript Metadata Integration

**This is the key unlock.** Once timing metadata is preserved, processing latency becomes irrelevant and deferred/batched transcription is viable.

Currently `run_summarization.py`:

1. Extracts date from filename (`YYYYMMDD-*.txt`) or file mtime
2. Filters calendar entries to that date
3. Uses LLM to disambiguate between multiple meetings via participant matching, topic analysis, and recent notes history

**Problem:** When multiple meetings occur on the same day (common for back-to-back meetings), disambiguation relies on the LLM—which usually works but can fail.

**Solution:** With meeting start/end times, calendar matching becomes deterministic (time overlap) rather than heuristic (content analysis).

### Solution: Embed Metadata in Transcript Header

Add a structured header at the top of transcripts. This keeps the processor working with arbitrary transcripts (no header = use current fallback behavior).

#### Proposed Header Format

```
---
meeting_start: 2026-01-27T14:00:00-08:00
meeting_end: 2026-01-27T15:03:00-08:00
recording_source: macwhisper
---

[Transcript content follows...]
```

**Fields:**
- `meeting_start` / `meeting_end`: ISO 8601 timestamps (critical for calendar matching)
- `recording_source`: Optional, identifies the transcription tool
- Future fields: `speakers`, `audio_file`, `confidence`, etc.

The YAML front matter format is familiar (used in Jekyll, Hugo, Obsidian) and easy to parse.

### Processor Changes Required

#### 1. Parse transcript header in `run_summarization.py`

```python
def parse_transcript_header(filepath: str) -> dict:
    """Extract YAML front matter from transcript if present."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Check for YAML front matter
    if not content.startswith('---'):
        return {}
    
    # Find closing ---
    end_match = re.search(r'\n---\n', content[3:])
    if not end_match:
        return {}
    
    yaml_text = content[3:3 + end_match.start()]
    try:
        import yaml
        return yaml.safe_load(yaml_text) or {}
    except:
        return {}
```

#### 2. Use meeting time for precise calendar matching

Update `process_transcript()` to:
```python
metadata = parse_transcript_header(input_file)

if metadata.get('meeting_start'):
    # Parse ISO timestamp
    meeting_start = datetime.fromisoformat(metadata['meeting_start'])
    meeting_date = meeting_start.strftime('%Y-%m-%d')
    meeting_time = meeting_start.strftime('%H:%M')
    
    # Filter calendar entries to those overlapping this time window
    day_entries = [e for e in calendar_entries 
                   if e['date'] == meeting_date
                   and time_overlaps(e, meeting_start, meeting_end)]
```

#### 3. Update LLM prompt with time context

Add to `build_calendar_aware_prompt()`:
```python
if meeting_time:
    calendar_instructions += f"""
## MEETING TIME FROM TRANSCRIPT
The recording started at {meeting_time} and ended at {meeting_end_time}.
This STRONGLY constrains which calendar entry matches:
- Prefer entries whose time slot contains or overlaps this window
- A 14:00-15:03 recording almost certainly matches a 14:00-15:00 calendar slot
"""
```

### Source Integration

Each transcription source emits the header differently:

| Source | How to emit header |
|--------|-------------------|
| MacWhisper webhook | `meetingnotesd.py` adds header from optional payload fields or receipt time |
| Watch folder | Post-processing script adds header based on file timestamps |
| Transcriber appliance | Appliance POSTs transcript with header already embedded (has precise start/end from `/start` and `/stop` API calls) |
| Manual drop in inbox | No header — processor falls back to filename date / mtime |

#### MacWhisper Webhook Integration

The `meetingnotesd.py` webhook currently receives:
- `title`: Meeting title from MacWhisper
- `transcript`: Full transcript text

Update to add metadata header:

```python
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    title = data['title']
    transcript = data['transcript']
    
    # Check for optional timing fields (if MacWhisper sends them)
    meeting_start = data.get('start_time') or data.get('meeting_start')
    meeting_end = data.get('end_time') or data.get('meeting_end')
    duration = data.get('duration')  # seconds
    
    # Build metadata header
    now = datetime.now().astimezone()
    header_lines = ['---']
    
    if meeting_start:
        header_lines.append(f'meeting_start: {meeting_start}')
    if meeting_end:
        header_lines.append(f'meeting_end: {meeting_end}')
    elif not meeting_start:
        # Fallback: use receipt time as meeting_end
        header_lines.append(f'meeting_end: {now.isoformat()}')
    
    if duration:
        # If we have duration but not start, estimate start
        if not meeting_start and meeting_end:
            start = now - timedelta(seconds=duration)
            header_lines.append(f'meeting_start: {start.isoformat()}')
    
    header_lines.append('recording_source: macwhisper')
    header_lines.append('---')
    header_lines.append('')
    
    transcript_with_header = '\n'.join(header_lines) + transcript
    # ... save to inbox
```

**Note:** MacWhisper is no longer the primary transcription source — the transcriber appliance handles recording and transcription. MacWhisper webhook support remains for backward compatibility but the investigation items below are no longer a priority.

~~**Investigation needed:**~~
- ~~Check MacWhisper webhook documentation for available fields~~
- ~~Test what MacWhisper actually sends in the POST body~~
- ~~Check if MacWhisper Pro has additional metadata options~~

### ~~Experiment Checklist~~ (All implemented)

- [x] ~~Check MacWhisper webhook payload~~ — Transcriber appliance is now primary source
- [x] Header parsing implemented in run_summarization.py (`parse_transcript_header()`)
- [x] Calendar filtering with time overlap implemented
- [x] Prompt updated with time context (`build_calendar_aware_prompt()`)
- [ ] Measure improvement in calendar matching accuracy (qualitative: working well in practice)

---

## Notes

- `taskpolicy` does not work for MacWhisper — tested and ruled out (Feb 2026)
- MacWhisper is built on whisper.cpp (by Georgi Gerganov)
- whisper.cpp supports Core ML for faster inference on Apple Silicon; M1 Mac Mini achieves ~10x real-time
- whisper.cpp tinydiarize (`-tdrz`) was evaluated and rejected — only `small.en` model available, project stalled
- **Timestamps + calendar context + LLM handles speaker identification well** — explicit diarization (pyannote) unnecessary
- Whisper timestamps now enabled on pilot (removed `--no-timestamps` flag); prompt.txt updated to use them
- YAML front matter parsing uses `pyyaml` (in inline script metadata)
- Tailscale provides stable mesh networking; VBAN works well over home network
- M1 Mac Mini set to "Never Sleep" + HDMI dummy plug for headless operation
- SoundSource was evaluated but replaced by BlackHole 2ch + `vban_send.py --mic` dual-input mixing
- VBAN capture is now built directly into transcriber.py (VBANCapture class) — no ffmpeg or separate receiver needed
