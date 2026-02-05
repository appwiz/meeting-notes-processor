#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "fastapi>=0.115.0",
#     "uvicorn>=0.34.0",
#     "httpx>=0.28.0",
#     "pyyaml>=6.0.0",
# ]
# ///
"""
Transcriber — FastAPI server for the Mac Mini transcription appliance.

Manages audio recording via ffmpeg and transcription via whisper.cpp.
On completion, POSTs results to meetingnotesd.py with YAML front matter
containing meeting start/end timestamps.

Run with: uv run transcriber.py
Config:   Set WEBHOOK_URL to your meetingnotesd endpoint (default: http://localhost:9876/webhook)

API:
  GET  /status      — Health check, active recordings, disk space
  POST /start       — Begin recording: {"title": "Meeting Name"}
  POST /stop        — Stop recording, queue for transcription
  GET  /recordings  — List recent recordings and status
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WHISPER_CLI = os.getenv("WHISPER_CLI", os.path.expanduser("~/whisper.cpp/build/bin/whisper-cli"))
WHISPER_MODEL = os.getenv("WHISPER_MODEL", os.path.expanduser("~/whisper.cpp/models/ggml-large-v3.bin"))
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", os.path.expanduser("~/transcriber/recordings")))
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://nuctu:9876/webhook")
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE", "BlackHole 2ch")  # macOS audio input
HOST = os.getenv("TRANSCRIBER_HOST", "0.0.0.0")
PORT = int(os.getenv("TRANSCRIBER_PORT", "8000"))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("transcriber")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class RecordingState(str, Enum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


class Recording:
    def __init__(self, title: str, audio_path: Path):
        self.title = title
        self.audio_path = audio_path
        self.transcript_path: Optional[Path] = None
        self.state = RecordingState.RECORDING
        self.meeting_start = datetime.now(timezone.utc)
        self.meeting_end: Optional[datetime] = None
        self.error: Optional[str] = None
        self.ffmpeg_process: Optional[subprocess.Popen] = None
        self.webhook_sent = False

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "state": self.state.value,
            "audio_path": str(self.audio_path),
            "transcript_path": str(self.transcript_path) if self.transcript_path else None,
            "meeting_start": self.meeting_start.isoformat(),
            "meeting_end": self.meeting_end.isoformat() if self.meeting_end else None,
            "error": self.error,
            "webhook_sent": self.webhook_sent,
        }


# Active recording (only one at a time)
active_recording: Optional[Recording] = None
# Recent completed recordings (ring buffer)
recent_recordings: list[Recording] = []
MAX_RECENT = 20

# Background transcription task
_transcription_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Transcriber", version="0.1.0")


class StartRequest(BaseModel):
    title: str


class StopResponse(BaseModel):
    status: str
    title: str
    duration_seconds: float
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _disk_free_gb() -> float:
    """Return free disk space in GB."""
    stat = os.statvfs(str(RECORDINGS_DIR))
    return (stat.f_bavail * stat.f_frsize) / (1024**3)


def _list_audio_devices() -> str:
    """List available macOS audio input devices via ffmpeg."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True, text=True, timeout=10,
        )
        return result.stderr  # ffmpeg outputs device list to stderr
    except Exception as e:
        return f"Error listing devices: {e}"


def _start_ffmpeg(audio_path: Path) -> subprocess.Popen:
    """Start ffmpeg to record from the virtual audio device."""
    # Record as 16-bit PCM WAV at 16kHz (whisper's native format)
    cmd = [
        "ffmpeg",
        "-f", "avfoundation",
        "-i", f":{AUDIO_DEVICE}",   # colon prefix = audio-only device
        "-ar", "16000",              # 16kHz sample rate
        "-ac", "1",                  # mono
        "-c:a", "pcm_s16le",        # 16-bit PCM
        "-y",                        # overwrite
        str(audio_path),
    ]
    logger.info(f"Starting ffmpeg: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return proc


async def _transcribe(recording: Recording) -> None:
    """Run whisper.cpp on the recorded audio and post result to webhook."""
    recording.state = RecordingState.TRANSCRIBING
    transcript_path = recording.audio_path.with_suffix(".txt")

    try:
        # Run whisper.cpp
        cmd = [
            WHISPER_CLI,
            "-m", WHISPER_MODEL,
            "-f", str(recording.audio_path),
            "-l", "en",
            "--no-timestamps",
            "--print-progress",
        ]
        logger.info(f"Starting transcription: {recording.title}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()[-500:]
            recording.state = RecordingState.FAILED
            recording.error = f"whisper-cli failed: {error_msg}"
            logger.error(f"Transcription failed for {recording.title}: {recording.error}")
            return

        transcript_text = stdout.decode().strip()
        if not transcript_text:
            recording.state = RecordingState.FAILED
            recording.error = "whisper-cli produced empty output"
            logger.error(f"Empty transcription for {recording.title}")
            return

        # Write transcript to file for reference
        transcript_path.write_text(transcript_text)
        recording.transcript_path = transcript_path

        # Build YAML front matter with timestamps
        local_tz = datetime.now(timezone.utc).astimezone().tzinfo
        start_local = recording.meeting_start.astimezone(local_tz)
        end_local = recording.meeting_end.astimezone(local_tz) if recording.meeting_end else start_local

        front_matter = (
            "---\n"
            f"meeting_start: {start_local.isoformat()}\n"
            f"meeting_end: {end_local.isoformat()}\n"
            f"recording_source: transcriber\n"
            "---\n\n"
        )
        full_transcript = front_matter + transcript_text

        # POST to meetingnotesd webhook
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                WEBHOOK_URL,
                json={"title": recording.title, "transcript": full_transcript},
            )
            if resp.status_code == 200:
                recording.webhook_sent = True
                logger.info(f"Transcript posted to webhook for: {recording.title}")
            else:
                logger.warning(
                    f"Webhook returned {resp.status_code} for {recording.title}: {resp.text[:200]}"
                )

        recording.state = RecordingState.COMPLETED
        logger.info(f"Transcription complete: {recording.title}")

    except Exception as e:
        recording.state = RecordingState.FAILED
        recording.error = str(e)
        logger.error(f"Transcription error for {recording.title}: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/status")
async def status():
    """Health check with system info."""
    return {
        "status": "ok",
        "service": "transcriber",
        "recording": active_recording.to_dict() if active_recording else None,
        "disk_free_gb": round(_disk_free_gb(), 1),
        "whisper_model": WHISPER_MODEL,
        "webhook_url": WEBHOOK_URL,
        "audio_device": AUDIO_DEVICE,
        "recent_count": len(recent_recordings),
    }


@app.post("/start")
async def start(req: StartRequest):
    """Begin recording audio."""
    global active_recording

    if active_recording and active_recording.state == RecordingState.RECORDING:
        raise HTTPException(
            status_code=409,
            detail=f"Already recording: {active_recording.title}",
        )

    # Create recordings directory
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    # Generate audio filename
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in req.title)
    safe_title = safe_title.strip().replace(" ", "-")[:50]
    audio_path = RECORDINGS_DIR / f"{ts}-{safe_title}.wav"

    recording = Recording(title=req.title, audio_path=audio_path)

    try:
        recording.ffmpeg_process = _start_ffmpeg(audio_path)
        # Give ffmpeg a moment to start
        await asyncio.sleep(0.5)
        if recording.ffmpeg_process.poll() is not None:
            stderr = recording.ffmpeg_process.stderr.read().decode() if recording.ffmpeg_process.stderr else ""
            raise RuntimeError(f"ffmpeg exited immediately: {stderr[-300:]}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start recording: {e}")

    active_recording = recording
    logger.info(f"Recording started: {req.title} → {audio_path}")

    return {
        "status": "recording",
        "title": req.title,
        "audio_path": str(audio_path),
        "meeting_start": recording.meeting_start.isoformat(),
    }


@app.post("/stop")
async def stop():
    """Stop recording, queue for transcription."""
    global active_recording, _transcription_task

    if not active_recording or active_recording.state != RecordingState.RECORDING:
        raise HTTPException(status_code=404, detail="No active recording")

    recording = active_recording
    recording.meeting_end = datetime.now(timezone.utc)

    # Stop ffmpeg gracefully
    if recording.ffmpeg_process and recording.ffmpeg_process.poll() is None:
        recording.ffmpeg_process.send_signal(signal.SIGINT)
        try:
            recording.ffmpeg_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            recording.ffmpeg_process.kill()
            recording.ffmpeg_process.wait()

    duration = (recording.meeting_end - recording.meeting_start).total_seconds()
    logger.info(f"Recording stopped: {recording.title} ({duration:.0f}s)")

    # Verify audio file exists and has content
    if not recording.audio_path.exists() or recording.audio_path.stat().st_size < 1000:
        recording.state = RecordingState.FAILED
        recording.error = "Audio file missing or too small"
        _archive_recording(recording)
        active_recording = None
        raise HTTPException(status_code=500, detail="Recording failed: no audio captured")

    # Queue transcription in background
    _transcription_task = asyncio.create_task(_transcribe(recording))
    _transcription_task.add_done_callback(lambda _: _archive_recording(recording))

    active_recording = None

    return StopResponse(
        status="transcribing",
        title=recording.title,
        duration_seconds=round(duration, 1),
        message=f"Transcription queued. Audio: {recording.audio_path.name}",
    )


def _archive_recording(recording: Recording) -> None:
    """Move recording to recent list."""
    recent_recordings.insert(0, recording)
    while len(recent_recordings) > MAX_RECENT:
        recent_recordings.pop()


@app.get("/recordings")
async def recordings():
    """List recent recordings."""
    items = [r.to_dict() for r in recent_recordings]
    if active_recording:
        items.insert(0, active_recording.to_dict())
    return {"recordings": items, "total": len(items)}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup():
    """Validate environment on startup."""
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    if not Path(WHISPER_CLI).exists():
        logger.warning(f"whisper-cli not found at {WHISPER_CLI}")
    if not Path(WHISPER_MODEL).exists():
        logger.warning(f"Whisper model not found at {WHISPER_MODEL}")

    logger.info(f"Transcriber starting on {HOST}:{PORT}")
    logger.info(f"  Whisper CLI:  {WHISPER_CLI}")
    logger.info(f"  Model:        {WHISPER_MODEL}")
    logger.info(f"  Recordings:   {RECORDINGS_DIR}")
    logger.info(f"  Webhook URL:  {WEBHOOK_URL}")
    logger.info(f"  Audio device: {AUDIO_DEVICE}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
