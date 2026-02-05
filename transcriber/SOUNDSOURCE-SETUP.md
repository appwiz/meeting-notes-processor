# SoundSource Audio Routing Setup

Configure SoundSource to route meeting audio into BlackHole 2ch for transcription.

## Prerequisites

- **SoundSource** installed (Rogue Amoeba) — you have this
- **BlackHole 2ch** installed (`brew install blackhole-2ch`) — ✅ done
- **Reboot** completed after BlackHole install (required for audio driver to load)

## What This Does

```
┌─────────── SoundSource ──────────┐
│                                  │
│  Zoom audio ─┬─► Speakers        │
│              └─► BlackHole 2ch   │
│                                  │
│  Teams audio ┬─► Speakers        │
│              └─► BlackHole 2ch   │
│                                  │
└──────────────────────────────────┘

┌─────────── vban_send.py ─────────┐
│                                  │
│  BlackHole 2ch ──► ┐             │
│  (remote audio)    ├─ mix ─► VBAN → pilot
│  Your mic ────────► ┘             │
│  (your voice)                    │
└──────────────────────────────────┘
```

SoundSource routes remote participant audio (Zoom/Teams) to BlackHole.
The VBAN sender captures from BlackHole AND your microphone simultaneously,
mixing both streams in software. You hear the meeting normally through
your speakers. Both sides of the conversation reach pilot for transcription.

> **Note:** SoundSource handles per-app *output* routing — it cannot
> route microphone input to another device. Mic capture is handled
> automatically by the VBAN sender's dual-input mixing mode.

## Step-by-Step Setup

### 1. Verify BlackHole 2ch appears

After rebooting, open **System Settings → Sound**. You should see
"BlackHole 2ch" listed under both Input and Output devices.
Don't set it as your default — it's only for routing.

### 2. Open SoundSource

Click the SoundSource icon in the menu bar (🔈 with a grid).

### 3. Configure Zoom

1. Look for **zoom.us** in the Applications list (appears when Zoom is running,
   or click **+** to add it)
2. Click the **Output** dropdown for Zoom
3. Select **Multi-Output** → then check both:
   - ✅ Your normal output (e.g., "Yeti Stereo Microphone" or "LG HDR 4K" or whatever you use for speakers/headphones)
   - ✅ **BlackHole 2ch**
4. This creates a split: Zoom audio goes to both your ears AND BlackHole

### 4. Configure Microsoft Teams

1. Find **Microsoft Teams** in the Applications list
2. Same as Zoom: set Output to Multi-Output with your speakers + BlackHole 2ch

### 5. Test the routing

```bash
# From the meeting-notes-processor/transcriber directory:

# List devices — you should see BlackHole 2ch as an input and your mic
uv run meeting.py devices

# Check status
uv run meeting.py status

# Quick test: start streaming with both BlackHole + mic
uv run vban/vban_send.py -d "BlackHole 2ch" --mic Yeti -t pilot
# (speak into mic, play a Zoom test meeting)
# Ctrl+C to stop

# Check pilot received audio
cd transcriber && make logs-vban
```

## Daily Usage

Once SoundSource is configured, you never touch it again. Just:

```bash
# Start of meeting:
uv run meeting.py start "Weekly Standup"

# End of meeting:
uv run meeting.py stop

# Check status anytime:
uv run meeting.py status
```

The `meeting.py` script auto-detects BlackHole as the primary audio source
and your mic (Yeti > built-in) for mixing. It starts the VBAN sender in
dual-input mode, triggers recording on pilot, and handles cleanup.

## Troubleshooting

### BlackHole 2ch not appearing
- Did you reboot after `brew install blackhole-2ch`?
- Check: `system_profiler SPAudioDataType | grep -i blackhole`

### No audio reaching pilot
- Check VBAN receiver: `cd transcriber && make logs-vban`
- Verify SoundSource routing: Play audio in Zoom → check SoundSource meters
- Test direct: `uv run vban/vban_send.py -d "BlackHole 2ch" --mic Yeti -t pilot --debug`

### Only hearing remote side (no mic)
- Check mic detection: `uv run meeting.py devices` (should show 🎤 line)
- Specify mic manually: `uv run meeting.py start "Test" -m "Yeti Stereo"`
- Check VBAN sender log: `cat /tmp/meeting-vban-sender.log` (should say "mixed mode")

### Only hearing your voice (no remote audio)
- Verify SoundSource is routing Zoom/Teams output to BlackHole 2ch
- Check SoundSource shows the Multi-Output with BlackHole checked

### Echo or feedback
- Make sure BlackHole is NOT set as your default output
- BlackHole should only receive audio via SoundSource routing, not directly

## SoundSource Profiles (Optional)

If you want to quickly toggle the routing:
1. In SoundSource, set up the routing as described above
2. Save as a **Profile** (e.g., "Meeting Recording")
3. Create a second profile with normal routing (no BlackHole)
4. Switch profiles from the menu bar when starting/ending meetings
