# SoundSource Audio Routing Setup

Configure SoundSource to route meeting audio into BlackHole 2ch for transcription.

## Prerequisites

- **SoundSource** installed (Rogue Amoeba) — you have this
- **BlackHole 2ch** installed (`brew install blackhole-2ch`) — ✅ done
- **Reboot** completed after BlackHole install (required for audio driver to load)

## What This Does

```
┌─────────────── SoundSource ─────────────────┐
│                                              │
│  Zoom audio ──┬──► Your speakers (hear it)   │
│               └──► BlackHole 2ch (capture)   │
│                                              │
│  Teams audio ─┬──► Your speakers (hear it)   │
│               └──► BlackHole 2ch (capture)   │
│                                              │
│  Your mic ────────► BlackHole 2ch (capture)   │
│  (via Audio Tap)                             │
│                                              │
└──────────────────────────────────────────────┘
        │
   BlackHole 2ch (input device)
        │
   vban_send.py → VBAN/UDP → pilot
```

You hear the meeting normally. BlackHole captures both sides of the
conversation. The VBAN sender streams it to pilot for transcription.

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

### 5. Route your microphone to BlackHole (captures your side)

This step captures YOUR voice alongside the remote participants:

1. In SoundSource, scroll to the **Input Devices** section at the bottom
2. Find your microphone (e.g., "Yeti Stereo Microphone")
3. Click **Effects** or the routing options
4. Use **Audio Tap** or **Routing** to send a copy to BlackHole 2ch

**Alternative approach** (if Audio Tap isn't available):
1. Open **Audio MIDI Setup** (Spotlight → "Audio MIDI Setup")
2. Click **+** → **Create Multi-Output Device**
3. Check: ✅ Your speakers, ✅ BlackHole 2ch
4. In SoundSource, set Zoom/Teams output to this Multi-Output Device

### 6. Test the routing

```bash
# From the meeting-notes-processor/transcriber directory:

# List devices — you should see BlackHole 2ch as an input
uv run meeting.py devices

# Check status
uv run meeting.py status

# Quick test: start streaming, play audio in Zoom
uv run vban/vban_send.py -d "BlackHole 2ch" -t pilot
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

The `meeting.py` script auto-detects BlackHole as the best audio source,
starts VBAN streaming, triggers recording on pilot, and handles cleanup.

## Troubleshooting

### BlackHole 2ch not appearing
- Did you reboot after `brew install blackhole-2ch`?
- Check: `system_profiler SPAudioDataType | grep -i blackhole`

### No audio reaching pilot
- Check VBAN receiver: `cd transcriber && make logs-vban`
- Verify SoundSource routing: Play audio in Zoom → check SoundSource meters
- Test direct: `uv run vban/vban_send.py -d "BlackHole 2ch" -t pilot --debug`

### Only hearing one side of conversation
- Ensure Step 5 is done (mic → BlackHole routing)
- SoundSource should show your mic being routed to BlackHole

### Echo or feedback
- Make sure BlackHole is NOT set as your default output
- BlackHole should only receive audio via SoundSource routing, not directly

## SoundSource Profiles (Optional)

If you want to quickly toggle the routing:
1. In SoundSource, set up the routing as described above
2. Save as a **Profile** (e.g., "Meeting Recording")
3. Create a second profile with normal routing (no BlackHole)
4. Switch profiles from the menu bar when starting/ending meetings
