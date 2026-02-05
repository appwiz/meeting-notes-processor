# Transcription Workflow Optimization Plan

**Date:** January 27, 2026  
**Status:** Draft - Pending Review

**Immediate Action:** Use `taskpolicy -c background` with MacWhisper to reduce CPU contention during back-to-back meetings.

## Problem Statement

Current workflow uses MacWhisper for local transcription with webhook integration. Two issues:

1. **No speaker identification** when webhook integration is in use
2. **High CPU load** during transcription makes Zoom/Teams unusable for back-to-back meetings

**Constraint:** Uses both Zoom and Teams—solution must work with both platforms.

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

## Option 1: CPU Throttling with `taskpolicy` (Fallback Only)

**Only relevant if timing metadata cannot be preserved.** If we capture meeting start/end times, deferred processing has no downside and this option is unnecessary.

macOS has `taskpolicy` which can clamp processes to background QoS:

```bash
# Run MacWhisper at "background" priority - lowest CPU/IO scheduling
taskpolicy -c background /Applications/MacWhisper.app/Contents/MacOS/MacWhisper

# Or throttle an already-running process by PID
taskpolicy -b -p $(pgrep -f MacWhisper)
```

The `-c background` clamp tells the scheduler this is maintenance work that should yield to everything else.

### Pros
- Zero cost
- No workflow changes
- Immediate solution

### Cons
- Only needed if we can't defer processing
- Might make transcription very slow
- MacWhisper may not work well when throttled

### Experiment
- [ ] Only pursue if timing metadata capture fails

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

## Option 3: DIY Pipeline with whisper.cpp (Speaker Turn Detection)

For maximum control, build a custom pipeline. **Full diarization (pyannote) is likely unnecessary**—calendar context handles speaker identification well. Speaker turn markers are sufficient.

### Components

1. **Audio capture**: Record system audio during meeting (trivial CPU)
2. **Timestamp capture**: Record meeting start/end times
3. **Queue**: Store recordings for processing
4. **Transcription**: whisper.cpp with `-tdrz` flag for speaker turn detection
5. **Header injection**: Add timing metadata to transcript

### whisper.cpp Speaker Turn Detection

whisper.cpp has built-in **tinydiarize** support:

```bash
# Download diarization-compatible model
./models/download-ggml-model.sh small.en-tdrz

# Run with speaker turn detection
./whisper-cli -f meeting.wav -m models/ggml-small.en-tdrz.bin -tdrz
```

Output includes `[SPEAKER_TURN]` markers:
```
[00:00:00.000 --> 00:00:03.800]   Okay Houston, we've had a problem here. [SPEAKER_TURN]
[00:00:03.800 --> 00:00:06.200]   This is Houston. Say again please. [SPEAKER_TURN]
```

The LLM + calendar context can then identify WHO is speaking at each turn—no explicit speaker labels needed.

### Why Not pyannote?

pyannote provides speaker embedding (identifying individual speakers), but:
- Requires GPU for good performance
- Adds significant complexity
- Calendar context already provides participant list
- LLM can infer speaker identity from content + turn markers

**Speaker turn detection + calendar = good enough.**

### Pros
- Full control over pipeline
- No ongoing costs (except compute)
- Can integrate directly with meetingnotesd.py
- Privacy preserved
- Simpler than full diarization

### Cons
- Development effort required
- Need to set up audio recording workflow
- Need to replicate MacWhisper's call detection UI

### Call Detection UI Requirements

MacWhisper (beta) has call detection that prompts to record when a call starts. A DIY pipeline should replicate this:

**Start Recording:**
- Detect Zoom/Teams call starting (process launch or audio activity)
- Pop-up notification: "Starting recording" with 10-second countdown
- If not canceled, begin recording system audio
- Capture `meeting_start` timestamp
- Menu bar icon indicates recording in progress

**Stop Recording:**
- Detect call ending (process exit or audio silence)
- Pop-up notification: "Stopping recording" with countdown
- If not canceled, finalize recording
- Capture `meeting_end` timestamp
- Queue audio for transcription

**Manual Control:**
- Menu bar app with start/stop recording
- Keyboard shortcut for quick toggle
- Status indicator (recording/idle)

**Implementation options:**
- SwiftUI menu bar app (native macOS)
- Rumps (Python menu bar framework)
- Hammerspoon (Lua scripting for macOS automation)

### Experiment
- [ ] Build proof-of-concept with whisper.cpp tinydiarize
- [ ] Verify LLM speaker identification with turn markers + calendar
- [ ] Prototype call detection (Zoom/Teams process monitoring)
- [ ] Prototype menu bar UI with recording controls
- [ ] Prototype integration with run_summarization.py

---

## Option 4: Extend meetingnotesd.py for Audio Queuing

Modify the existing daemon to accept audio recordings (not just transcripts):

```
POST /webhook/audio
  - Receives audio file + timestamps
  - Queues for processing
  - Processes when system is idle (or on schedule)
  - Runs through whisper.cpp (optionally with -tdrz)
  - Injects timing metadata header
  - Triggers summarization pipeline
```

This builds on existing infrastructure and keeps the workflow familiar.

### Pros
- Builds on existing infrastructure
- Consistent webhook-based workflow
- Timing metadata naturally captured at recording time

### Cons
- Development effort required
- Need to find/build audio recording solution that sends webhooks

---

## Comparison Matrix

| Option | Preserves Timing | Speaker Turns | CPU Impact | Complexity | Notes |
|--------|-----------------|---------------|------------|------------|-------|
| 1. taskpolicy | ❌ | ❌ | ⚠️ Reduced | Low | Fallback only |
| 2. Watch Folder | ✅ (with work) | ❌ | ✅ Deferred | Medium | Need timestamp capture |
| 3. DIY Pipeline | ✅ | ✅ | ✅ Deferred | Medium | Recommended |
| 4. Extended Daemon | ✅ | ✅ | ✅ Deferred | Medium | Builds on existing infra |

---

## Recommended Phased Approach

### Phase 1: Timing Metadata (Priority - This Week)
Once timing metadata is captured, processing latency becomes irrelevant.

- [ ] Check MacWhisper webhook payload for meeting start time or duration
- [ ] Add YAML header to `meetingnotesd.py` webhook handler
- [ ] Add header parsing to `run_summarization.py`
- [ ] Update calendar matching to use time overlap filtering
- [ ] Update LLM prompt with time context

### Phase 2: Deferred Processing (Next)
With timing preserved, defer all transcription to idle periods.

- [ ] Test MacWhisper Watch Folder for recording-only mode
- [ ] Build audio recording workflow that captures timestamps
- [ ] Process transcriptions in batch during breaks (no rush)

### Phase 3: Speaker Turn Detection (Optional Enhancement)
May not be needed—calendar context handles speaker ID well.

- [ ] Test whisper.cpp `-tdrz` flag output
- [ ] Verify LLM speaker identification with turn markers + calendar
- [ ] Only implement if speaker confusion persists

### Deferred: CPU Throttling
Only pursue if timing metadata capture fails.

- [ ] `taskpolicy -c background` with MacWhisper

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

Each transcription option would need to emit this header:

| Option | How to emit header |
|--------|-------------------|
| MacWhisper webhook | Modify `meetingnotesd.py` to add header before saving |
| Watch folder | Post-processing script adds header based on file timestamps |
| DIY pipeline | Build header from recording metadata |
| Audio webhook | Daemon adds header when queuing audio |

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

- MacWhisper is built on whisper.cpp (by Georgi Gerganov)
- whisper.cpp supports Core ML for faster inference on Apple Silicon
- whisper.cpp tinydiarize detects speaker turns but doesn't identify speakers
- **Calendar context + LLM handles speaker identification well**—explicit diarization likely unnecessary
- pyannote (full diarization) deferred—only pursue if speaker confusion persists despite calendar context
- YAML front matter parsing requires `pyyaml` dependency (add to inline script metadata)
