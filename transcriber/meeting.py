#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31.0",
#     "sounddevice>=0.5.0",
# ]
# ///
"""
Meeting Recorder — one-command meeting capture and transcription.

Manages the full pipeline: VBAN streaming → pilot recording → transcription.
Designed to be the single command you run when a meeting starts.

Usage:
  uv run meeting.py start "Weekly Standup"       # begin recording
  uv run meeting.py stop                          # stop and transcribe
  uv run meeting.py status                        # check what's happening
  uv run meeting.py devices                       # list audio input devices

Prerequisites:
  - BlackHole 2ch installed on laptop
  - SoundSource routing Zoom/Teams + mic → BlackHole 2ch
  - VBAN receiver running on pilot (make deploy-vban)
  - Transcriber running on pilot (make deploy)
"""

import argparse
import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests
import sounddevice as sd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://pilot:8000")
PILOT_HOST = os.getenv("PILOT_HOST", "pilot")
VBAN_PORT = int(os.getenv("VBAN_PORT", "6980"))

# Audio device preference order (first match wins)
DEVICE_PREFERENCE = [
    "BlackHole 2ch",       # Best: full meeting audio via SoundSource routing
    "ZoomAudioDevice",     # Fallback: Zoom remote participants only
    "Microsoft Teams",     # Fallback: Teams remote participants only
]

VBAN_SEND_SCRIPT = Path(__file__).parent / "vban" / "vban_send.py"
PID_FILE = Path(os.getenv("MEETING_PID_FILE", "/tmp/meeting-vban-sender.pid"))
LOG_FILE = Path(os.getenv("MEETING_LOG_FILE", "/tmp/meeting-vban-sender.log"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [meeting] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("meeting")

# ---------------------------------------------------------------------------
# Audio Device Discovery
# ---------------------------------------------------------------------------


def find_best_device() -> tuple[str, str]:
    """Find the best available audio input device.
    
    Returns (device_name, quality) where quality is 'full' or 'partial'.
    'full' means both sides of conversation (via BlackHole + SoundSource).
    'partial' means only remote participants (direct virtual device).
    """
    devices = sd.query_devices()
    available = {}
    for d in devices:
        if d["max_input_channels"] > 0:
            available[d["name"]] = d

    for pref in DEVICE_PREFERENCE:
        for name in available:
            if pref.lower() in name.lower():
                quality = "full" if "blackhole" in name.lower() else "partial"
                return name, quality

    return None, None


def list_devices():
    """Show available input devices with recommendations."""
    devices = sd.query_devices()
    print("\n📱 Audio Input Devices:")
    print("-" * 65)
    for d in devices:
        if d["max_input_channels"] > 0:
            name = d["name"]
            markers = []
            if "blackhole" in name.lower():
                markers.append("★ RECOMMENDED (full conversation)")
            elif "zoom" in name.lower():
                markers.append("⚡ Zoom (remote audio only)")
            elif "teams" in name.lower():
                markers.append("⚡ Teams (remote audio only)")
            marker_str = f"  {' '.join(markers)}" if markers else ""
            print(f"  [{d['index']}] {name} (ch:{d['max_input_channels']}){marker_str}")
    print()


# ---------------------------------------------------------------------------
# VBAN Sender Management
# ---------------------------------------------------------------------------


def _sender_running() -> int | None:
    """Check if VBAN sender is running. Returns PID or None."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Check if process exists
        return pid
    except (ProcessLookupError, ValueError):
        PID_FILE.unlink(missing_ok=True)
        return None


def start_sender(device: str) -> int:
    """Start the VBAN sender in background. Returns PID."""
    existing = _sender_running()
    if existing:
        logger.info(f"VBAN sender already running (PID {existing})")
        return existing

    cmd = [
        sys.executable, str(VBAN_SEND_SCRIPT),
        "-d", device,
        "-t", PILOT_HOST,
        "-p", str(VBAN_PORT),
    ]

    log_fh = open(LOG_FILE, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # Detach from terminal
    )

    PID_FILE.write_text(str(proc.pid))
    logger.info(f"VBAN sender started (PID {proc.pid}) → {device}")
    return proc.pid


def stop_sender():
    """Stop the VBAN sender."""
    pid = _sender_running()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            # Wait briefly for clean shutdown
            for _ in range(10):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.2)
                except ProcessLookupError:
                    break
        except ProcessLookupError:
            pass
        PID_FILE.unlink(missing_ok=True)
        logger.info(f"VBAN sender stopped (PID {pid})")
    else:
        logger.info("No VBAN sender running")


# ---------------------------------------------------------------------------
# Transcriber API
# ---------------------------------------------------------------------------


def transcriber_status() -> dict | None:
    """Get transcriber status from pilot."""
    try:
        r = requests.get(f"{TRANSCRIBER_URL}/status", timeout=5)
        return r.json()
    except requests.RequestException as e:
        logger.error(f"Cannot reach transcriber at {TRANSCRIBER_URL}: {e}")
        return None


def transcriber_start(title: str) -> dict | None:
    """Start recording on pilot."""
    try:
        r = requests.post(
            f"{TRANSCRIBER_URL}/start",
            json={"title": title},
            timeout=10,
        )
        if r.status_code == 409:
            logger.warning(f"Already recording: {r.json().get('detail', '')}")
            return r.json()
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.error(f"Failed to start recording: {e}")
        return None


def transcriber_stop() -> dict | None:
    """Stop recording on pilot."""
    try:
        r = requests.post(f"{TRANSCRIBER_URL}/stop", timeout=10)
        if r.status_code == 404:
            logger.warning("No active recording to stop")
            return None
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.error(f"Failed to stop recording: {e}")
        return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_start(args):
    """Start a meeting recording."""
    title = args.title

    # 1. Check transcriber is reachable
    status = transcriber_status()
    if not status:
        print("❌ Cannot reach transcriber on pilot. Is it running?")
        print(f"   Try: cd transcriber && make status")
        sys.exit(1)

    if status.get("recording"):
        rec = status["recording"]
        print(f"⚠️  Already recording: {rec['title']}")
        print(f"   Started: {rec['meeting_start']}")
        print(f"   Use 'meeting.py stop' to end the current recording first.")
        sys.exit(1)

    # 2. Find audio device
    if args.device:
        device_name = args.device
        quality = "full" if "blackhole" in device_name.lower() else "partial"
    else:
        device_name, quality = find_best_device()

    if not device_name:
        print("❌ No suitable audio device found.")
        print("   Install BlackHole 2ch and configure SoundSource, or specify with -d")
        list_devices()
        sys.exit(1)

    if quality == "partial":
        print(f"⚠️  Using {device_name} — only remote participants will be captured.")
        print(f"   For full conversation capture, configure SoundSource → BlackHole 2ch")

    # 3. Start VBAN sender
    print(f"🎙  Audio source: {device_name}")
    sender_pid = start_sender(device_name)

    # Give VBAN a moment to connect
    time.sleep(3)

    # 4. Start recording on pilot
    print(f"🔴 Starting recording: {title}")
    result = transcriber_start(title)
    if result:
        print(f"✅ Recording! Audio streaming to pilot.")
        print(f"   Title: {title}")
        print(f"   Run 'uv run meeting.py stop' when done.")
    else:
        print("❌ Failed to start recording. Stopping sender...")
        stop_sender()
        sys.exit(1)


def cmd_stop(args):
    """Stop a meeting recording."""
    # 1. Stop recording on pilot (triggers transcription)
    print("⏹  Stopping recording...")
    result = transcriber_stop()
    if result:
        print(f"✅ Recording stopped: {result.get('title', '?')}")
        print(f"   Duration: {result.get('duration_seconds', '?')}s")
        print(f"   Transcription queued — will be posted to nuctu webhook automatically.")
    else:
        print("⚠️  No active recording found (may have already been stopped)")

    # 2. Stop VBAN sender
    stop_sender()
    print("🎙  Audio streaming stopped.")


def cmd_status(args):
    """Show current status."""
    # VBAN sender
    sender_pid = _sender_running()
    if sender_pid:
        print(f"🎙  VBAN sender: running (PID {sender_pid})")
    else:
        print(f"🎙  VBAN sender: not running")

    # Audio device
    device_name, quality = find_best_device()
    if device_name:
        q_label = "full conversation" if quality == "full" else "remote only"
        print(f"🔊 Best audio device: {device_name} ({q_label})")
    else:
        print(f"🔊 No suitable audio device found")

    # Transcriber
    status = transcriber_status()
    if status:
        recording = status.get("recording")
        if recording:
            print(f"🔴 Recording: {recording['title']} (started: {recording['meeting_start']})")
        else:
            print(f"⏸  Transcriber: idle")
        print(f"💾 Disk free: {status.get('disk_free_gb', '?')} GB")
        print(f"📊 Recent recordings: {status.get('recent_count', 0)}")
    else:
        print(f"❌ Transcriber: unreachable")


def cmd_devices(args):
    """List audio devices."""
    list_devices()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Meeting Recorder — one-command meeting capture and transcription",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s start "Weekly Standup"
  %(prog)s start "1:1 with Sarah" -d ZoomAudioDevice
  %(prog)s stop
  %(prog)s status
  %(prog)s devices
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # start
    p_start = subparsers.add_parser("start", help="Start recording a meeting")
    p_start.add_argument("title", help="Meeting title")
    p_start.add_argument(
        "-d", "--device",
        help="Audio device name (default: auto-detect BlackHole > Zoom > Teams)",
    )
    p_start.set_defaults(func=cmd_start)

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop recording and transcribe")
    p_stop.set_defaults(func=cmd_stop)

    # status
    p_status = subparsers.add_parser("status", help="Show current status")
    p_status.set_defaults(func=cmd_status)

    # devices
    p_devices = subparsers.add_parser("devices", help="List audio input devices")
    p_devices.set_defaults(func=cmd_devices)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
