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

Captures audio via VBAN (UDP) directly to WAV and transcribes via whisper.cpp.
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
import logging
import os
import socket
import threading
import time
import wave
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
VBAN_PORT = int(os.getenv("VBAN_PORT", "6980"))  # UDP port for VBAN audio packets
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
        self.vban_capture: Optional["VBANCapture"] = None
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


class VBANCapture:
    """Captures VBAN audio packets from the network directly to a WAV file.

    Replaces the previous receiver → BlackHole → ffmpeg chain with a single
    UDP listener that writes PCM data straight to disk.
    """

    HEADER_SIZE = 28
    MAGIC = b"VBAN"
    SR_TABLE = [
        6000, 12000, 24000, 48000, 96000, 192000, 384000,
        8000, 16000, 32000, 64000, 128000, 256000, 512000,
        11025, 22050, 44100, 88200, 176400, 352800, 705600,
    ]

    def __init__(self, audio_path: Path, port: int = 6980):
        self.audio_path = audio_path
        self.port = port
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.sample_rate: Optional[int] = None
        self.total_samples = 0

    def start(self):
        """Start capturing VBAN packets in a background thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="vban-capture",
        )
        self._thread.start()

    def stop(self):
        """Stop capturing and finalize the WAV file."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _capture_loop(self):
        """Receive VBAN packets and write PCM data to WAV."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.port))
        sock.settimeout(1.0)  # allow periodic stop checks

        wav_file: Optional[wave.Wave_write] = None

        try:
            while not self._stop_event.is_set():
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    continue

                if len(data) <= self.HEADER_SIZE or data[:4] != self.MAGIC:
                    continue

                # Parse sample rate and channels from VBAN header
                sr_index = data[4] & 0x1F
                channels = (data[6] & 0xFF) + 1
                pcm_data = data[self.HEADER_SIZE:]

                if wav_file is None:
                    self.sample_rate = (
                        self.SR_TABLE[sr_index]
                        if sr_index < len(self.SR_TABLE)
                        else 48000
                    )
                    wav_file = wave.open(str(self.audio_path), "wb")
                    wav_file.setnchannels(channels)
                    wav_file.setsampwidth(2)  # 16-bit
                    wav_file.setframerate(self.sample_rate)
                    logger.info(
                        f"VBAN capture: {self.sample_rate}Hz {channels}ch from {addr[0]}"
                    )

                wav_file.writeframes(pcm_data)
                self.total_samples += len(pcm_data) // (2 * channels)

        except Exception as e:
            logger.error(f"VBAN capture error: {e}", exc_info=True)
        finally:
            if wav_file:
                wav_file.close()
                duration = (
                    self.total_samples / self.sample_rate if self.sample_rate else 0
                )
                logger.info(
                    f"VBAN capture saved: {duration:.1f}s, "
                    f"{self.total_samples} samples → {self.audio_path}"
                )
            sock.close()


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
        "vban_port": VBAN_PORT,
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
        recording.vban_capture = VBANCapture(audio_path, port=VBAN_PORT)
        recording.vban_capture.start()
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

    # Stop VBAN capture and finalize WAV
    if recording.vban_capture:
        recording.vban_capture.stop()

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
    logger.info(f"  VBAN port:    {VBAN_PORT}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
