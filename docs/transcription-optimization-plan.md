# Transcription Workflow Optimization Plan

**Date:** January 27, 2026 (updated February 5, 2026)  
**Status:** Active — Phase 1 (Timing Metadata) in progress; Transcriber appliance design finalized

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
| **Audio Routing** | **SoundSource** | Per-app audio hijacking: only meeting audio is captured, not system sounds or music |
| **Audio Transport** | **VBAN (UDP)** | Low-latency audio streaming optimized for real-time; avoids VPN-induced stutter |
| **Transcription** | **whisper.cpp + CoreML** | C++ port of Whisper with CoreML support; ~10x real-time on M1 |

### Architecture

```
┌─────────────────────────┐         VBAN/UDP          ┌──────────────────────────┐
│    LAPTOP (Director)    │ ◄──── Tailscale ────────► │   MAC MINI (Appliance)   │
│                         │       Audio Stream         │                         │
│  SoundSource routes     │                           │  FastAPI server          │
│  Zoom/Teams audio ──────┼───────────────────────────┼─► ffmpeg records .wav    │
│                         │   POST /start, /stop       │  whisper.cpp transcribes│
│  Call detector ─────────┼───────────────────────────┼─► queues & processes     │
│  (meeting start/end)    │                           │                         │
│                         │      POST /webhook         │  Delivers transcript ───┼──► meetingnotesd.py
│                         │ ◄─────────────────────────┼──with YAML front matter │
└─────────────────────────┘                           └──────────────────────────┘
```

**Laptop (The Director):**
- Background process monitors for Zoom/Teams meeting windows
- SoundSource redirects meeting app audio to VBAN stream aimed at Mini's Tailscale IP
- On meeting start: `POST http://[mini-ip]:8000/start` with `meeting_start` timestamp
- On meeting end: `POST http://[mini-ip]:8000/stop` with `meeting_end` timestamp

**Mac Mini (The Appliance):**
- FastAPI server manages recording state
- `ffmpeg` captures incoming VBAN stream to local `.wav`
- On stop: queues audio for whisper.cpp transcription (background task)
- On completion: POSTs transcript with YAML front matter to `meetingnotesd.py` webhook

### Implementation Phases

**Phase A: Appliance Setup (Mac Mini)**
- [ ] Install Homebrew, Python 3.11+, FFmpeg
- [ ] Compile whisper.cpp with CoreML: `WHISPER_COREML=1 make -j`
- [ ] Generate CoreML model: `./models/generate-coreml-model.sh large-v3`
- [ ] Install BlackHole 2ch as virtual audio bridge for VBAN → FFmpeg
- [ ] Build FastAPI server with `/start`, `/stop`, `/status` endpoints
- [ ] Background task queue for non-blocking transcription

**Phase B: Audio Routing (Laptop)**
- [ ] Configure SoundSource Output Group: primary output + VBAN stream
- [ ] Set Zoom and Teams to use the Output Group
- [ ] Verify audio arrives cleanly on Mini over Tailscale

**Phase C: Call Detection & Automation (Laptop)**
- [ ] Build background daemon that monitors for Zoom/Teams meeting windows
- [ ] Auto-trigger `/start` on meeting detection, `/stop` on meeting end
- [ ] Menu bar indicator for recording status
- [ ] Manual override (start/stop keyboard shortcut)

**Phase D: Transcript Delivery**
- [ ] Mini POSTs completed transcript to `meetingnotesd.py` webhook
- [ ] Include YAML front matter with `meeting_start`, `meeting_end`, `recording_source: transcriber`
- [ ] Existing pipeline handles the rest (calendar matching, summarization)

### Speaker Turn Detection (Optional Enhancement)

whisper.cpp supports tinydiarize (`-tdrz` flag) for speaker turn markers:

```
[00:00:00.000 --> 00:00:03.800]   Okay Houston, we've had a problem here. [SPEAKER_TURN]
[00:00:03.800 --> 00:00:06.200]   This is Houston. Say again please. [SPEAKER_TURN]
```

The LLM + calendar context identifies WHO is speaking at each turn. Full pyannote diarization is likely overkill — turn markers + calendar = good enough.

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
| 2. Watch Folder | ✅ (with work) | ❌ | ✅ Deferred | Medium | Viable fallback |
| 3. Transcriber Appliance | ✅ (native) | ✅ (optional) | ✅ Zero | Medium-High | ⭐ Recommended |

---

## Recommended Phased Approach

### Phase 1: Timing Metadata in Processor (Priority — This Week)
Make the processing pipeline timestamp-aware. This is prerequisite for everything else and delivers value immediately (even with MacWhisper, we can inject receipt-time estimates).

- [ ] Add YAML front matter support to `meetingnotesd.py` webhook handler
- [ ] Add `parse_transcript_header()` to `run_summarization.py`
- [ ] Update calendar matching to use time overlap when timestamps available
- [ ] Re-add time context to `build_calendar_aware_prompt()` (with real timestamps, not mtime heuristics)
- [ ] Add `pyyaml` to inline script dependencies
- [ ] Tests for header parsing and time-based calendar filtering

### Phase 2: Build the Transcriber Appliance
Set up the M1 Mac Mini as a dedicated transcription server.

- [ ] Appliance setup: whisper.cpp + CoreML compilation
- [ ] FastAPI server with `/start`, `/stop`, `/status` endpoints
- [ ] ffmpeg VBAN capture pipeline
- [ ] Transcript delivery with YAML front matter to meetingnotesd.py

### Phase 3: Laptop Audio Routing & Call Detection
Automate the laptop side.

- [ ] SoundSource per-app audio routing to VBAN
- [ ] Call detection daemon (Zoom/Teams process monitoring)
- [ ] Menu bar UI with recording status and manual controls
- [ ] Auto-trigger `/start` and `/stop` on the Mini

### Phase 4: Speaker Turn Detection (Optional Enhancement)
May not be needed — calendar context handles speaker ID well.

- [ ] Test whisper.cpp `-tdrz` flag output quality
- [ ] Verify LLM speaker identification with turn markers + calendar
- [ ] Only implement if speaker confusion persists

---

## Implementation Strategy

All Transcriber appliance code lives in this repo under `transcriber/`. The Mac Mini ("pilot") is provisioned and deployed to entirely from the laptop via SSH. No manual setup required on pilot itself.

### Target Machine

- **Hostname:** `pilot` (reachable via `ssh edd@pilot`)
- **Hardware:** M1 Mac Mini, 8 GB RAM, arm64
- **OS:** macOS 26.2
- **Current state:** Minimal — no Homebrew, no ffmpeg, system Python only. Sleep already disabled.

### Repo Layout

```
transcriber/
├── Makefile                  # All remote ops via SSH (provision, deploy, logs, etc.)
├── README.md                 # Setup & usage docs
├── server/
│   ├── transcriber.py        # FastAPI server with /start, /stop, /status (PEP 723 inline deps)
│   └── transcribe.sh         # whisper.cpp wrapper: run model, emit YAML front matter
├── setup/
│   ├── 01-homebrew.sh        # Install Homebrew (idempotent)
│   ├── 02-dependencies.sh    # brew install ffmpeg blackhole-2ch; install uv
│   ├── 03-whisper.sh         # Clone & compile whisper.cpp with CoreML; download large-v3 model
│   └── 04-service.sh         # Install launchd plist, start service
└── com.transcriber.plist     # launchd service definition (auto-start on boot)
```

### Makefile Targets

All targets run from the laptop against pilot via SSH:

| Target | Description |
|--------|-------------|
| `make check` | Verify SSH connectivity, show pilot hardware/software status |
| `make provision` | Run all `setup/*.sh` scripts in order (idempotent, safe to re-run) |
| `make deploy` | `rsync` server code to `~/transcriber/` on pilot, restart launchd service |
| `make logs` | Tail the transcriber service logs on pilot |
| `make status` | Show service status + any active recordings |
| `make ssh` | Open an interactive shell on pilot |
| `make test` | Send a test audio clip and verify the full round-trip |
| `make model` | Download/update the whisper.cpp model on pilot |

### Technology Choices

| Component | Choice | Rationale |
|-----------|--------|-----------|
| **Server** | FastAPI via `uv run` | PEP 723 inline deps, consistent with rest of project |
| **Python** | System python3 + `uv` | Avoid Homebrew python; `uv` handles venvs transparently |
| **Provisioning** | Numbered shell scripts over SSH | Idempotent, simple, no Ansible dependency |
| **Service manager** | launchd plist | Native macOS, auto-start on boot, log management |
| **Deployment** | `rsync` + `launchctl restart` | Fast, no build step, atomic |
| **Whisper model** | large-v3 + CoreML | Best accuracy; ~3-4x real-time is fine since transcription is async |
| **Audio bridge** | BlackHole 2ch | Virtual audio device for VBAN → ffmpeg capture on pilot |

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
| `/start` | POST | Begin recording: spawns ffmpeg to capture VBAN audio; records `meeting_start` timestamp. Body: `{"title": "Meeting Name"}` |
| `/stop` | POST | Stop recording: kills ffmpeg, queues audio for whisper.cpp transcription. Records `meeting_end` timestamp |
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
| Setup scripts | `transcriber/setup/` | run via SSH, not deployed | pilot (via laptop) |
| Makefile | `transcriber/Makefile` | stays on laptop | laptop |
| launchd plist | `transcriber/com.transcriber.plist` | `~/Library/LaunchAgents/` on pilot | pilot |
| meetingnotesd.py | repo root | already running on laptop | laptop |
| Call detector (Phase 3) | `transcriber/director/` (future) | laptop | laptop |

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

**Investigation needed:**
- [ ] Check MacWhisper webhook documentation for available fields
- [ ] Test what MacWhisper actually sends in the POST body
- [ ] Check if MacWhisper Pro has additional metadata options

### Experiment Checklist

- [ ] Check MacWhisper webhook payload for start time or duration
- [ ] Prototype header parsing in run_summarization.py
- [ ] Test calendar filtering with time overlap
- [ ] Update prompt with time context
- [ ] Measure improvement in calendar matching accuracy

---

## Notes

- `taskpolicy` does not work for MacWhisper — tested and ruled out (Feb 2026)
- MacWhisper is built on whisper.cpp (by Georgi Gerganov)
- whisper.cpp supports Core ML for faster inference on Apple Silicon; M1 Mac Mini achieves ~10x real-time
- whisper.cpp tinydiarize detects speaker turns but doesn't identify speakers
- **Calendar context + LLM handles speaker identification well** — explicit diarization (pyannote) likely unnecessary
- pyannote deferred — only pursue if speaker confusion persists despite calendar context + turn markers
- YAML front matter parsing requires `pyyaml` dependency (add to inline script metadata)
- Tailscale provides stable mesh networking; VBAN over cellular quality needs testing
- M1 Mac Mini should be set to "Never Sleep" + HDMI dummy plug for headless ANE/GPU operation
- SoundSource enables per-app audio routing without kernel extensions on modern macOS
- BlackHole 2ch acts as virtual audio bridge on the Mini (VBAN → ffmpeg)
