#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rumps>=0.4.0",
#     "requests>=2.31.0",
#     "sounddevice>=0.5.0",
#     "pyobjc-framework-Quartz>=10.0",
# ]
# ///
"""
Meeting Bar — macOS menu bar app for automatic meeting recording.

Sits in the menu bar showing recording state. Detects Zoom/Teams meetings
and automatically starts/stops recording via the VBAN → pilot pipeline.

States:
  ⏸  Idle (black mic icon)
  🔴 Recording (red dot)
  ⚠️  Error (warning icon)

Meeting detection:
  - Zoom: checks for CptHost subprocess (reliable in-meeting indicator)
  - Teams: checks window titles for "Meeting" / "Call" patterns

Usage:
  uv run meeting_bar.py
"""

import datetime
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
import rumps
import sounddevice as sd

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://pilot:8000")
PILOT_HOST = os.getenv("PILOT_HOST", "pilot")
VBAN_PORT = int(os.getenv("VBAN_PORT", "6980"))
POLL_INTERVAL = int(os.getenv("MEETING_POLL_INTERVAL", "5"))  # seconds

# Audio device preference order
DEVICE_PREFERENCE = [
    "BlackHole 2ch",
    "ZoomAudioDevice",
    "Microsoft Teams",
]

VBAN_SEND_SCRIPT = Path(__file__).parent / "vban" / "vban_send.py"
PID_FILE = Path(os.getenv("MEETING_PID_FILE", "/tmp/meeting-vban-sender.pid"))
LOG_FILE = Path(os.getenv("MEETING_LOG_FILE", "/tmp/meeting-vban-sender.log"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_PATH = Path("/tmp/meeting-bar.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [meeting-bar] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("meeting-bar")

# ---------------------------------------------------------------------------
# Icons (using emoji titles for simplicity — rumps supports these natively)
# ---------------------------------------------------------------------------

ICON_IDLE = "🎙"
ICON_RECORDING = "🔴"
ICON_ERROR = "⚠️"

# ---------------------------------------------------------------------------
# Meeting Detection
# ---------------------------------------------------------------------------


def detect_zoom_meeting() -> bool:
    """Check if user is in a Zoom meeting by looking for CptHost process.

    CptHost is Zoom's content sharing / meeting host subprocess that only
    runs when actively in a meeting (not just when the app is open).
    """
    try:
        result = subprocess.run(
            ["pgrep", "-x", "CptHost"],
            capture_output=True,
            timeout=3,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def detect_teams_meeting() -> bool:
    """Check if user is in a Teams meeting by inspecting window titles.

    Uses Quartz CGWindowListCopyWindowInfo to find Teams windows with
    meeting-related titles. Requires Screen Recording permission for
    window title access (macOS will prompt on first use).
    """
    try:
        from Quartz import (
            CGWindowListCopyWindowInfo,
            kCGWindowListOptionOnScreenOnly,
            kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )

        windows = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
            kCGNullWindowID,
        )

        meeting_patterns = ["meeting with", "call with", "| meeting", "| call"]

        for w in windows or []:
            owner = w.get("kCGWindowOwnerName", "") or ""
            title = w.get("kCGWindowName", "") or ""

            if "teams" not in owner.lower():
                continue

            title_lower = title.lower()
            if any(pat in title_lower for pat in meeting_patterns):
                return True

    except ImportError:
        logger.warning("pyobjc-framework-Quartz not available — Teams detection disabled")
    except Exception as e:
        logger.debug(f"Teams detection error: {e}")

    return False


def detect_meeting() -> str | None:
    """Check if user is in any meeting.

    Returns the meeting app name ("Zoom" / "Teams") or None.
    """
    if detect_zoom_meeting():
        return "Zoom"
    if detect_teams_meeting():
        return "Teams"
    return None


# ---------------------------------------------------------------------------
# Audio Device Discovery (reused from meeting.py)
# ---------------------------------------------------------------------------


def find_best_device() -> tuple[str | None, str | None]:
    """Find the best available audio input device."""
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


def find_mic_device() -> str | None:
    """Find the microphone for dual-input mixing."""
    devices = sd.query_devices()
    default_idx = sd.default.device[0]

    if default_idx is not None and default_idx >= 0:
        default_dev = devices[default_idx]
        name = default_dev["name"].lower()
        if default_dev["max_input_channels"] > 0 and not any(
            skip in name for skip in ["blackhole", "zoom", "teams"]
        ):
            return default_dev["name"]

    for d in devices:
        if d["max_input_channels"] > 0:
            name = d["name"].lower()
            if not any(skip in name for skip in ["blackhole", "zoom", "teams"]):
                return d["name"]

    return None


# ---------------------------------------------------------------------------
# VBAN Sender Management (reused from meeting.py)
# ---------------------------------------------------------------------------


def _sender_running() -> int | None:
    """Check if VBAN sender is running. Returns PID or None."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, ValueError):
        PID_FILE.unlink(missing_ok=True)
        return None


def start_sender(device: str, mic: str | None = None) -> int:
    """Start the VBAN sender in background. Returns PID."""
    existing = _sender_running()
    if existing:
        logger.info(f"VBAN sender already running (PID {existing})")
        return existing

    cmd = [
        "uv", "run", str(VBAN_SEND_SCRIPT),
        "-d", device,
        "-t", PILOT_HOST,
        "-p", str(VBAN_PORT),
    ]
    if mic:
        cmd.extend(["--mic", mic])

    log_fh = open(LOG_FILE, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    PID_FILE.write_text(str(proc.pid))
    mode = f"mixed ({device} + {mic})" if mic else device
    logger.info(f"VBAN sender started (PID {proc.pid}) → {mode}")
    return proc.pid


def stop_sender():
    """Stop the VBAN sender."""
    pid = _sender_running()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
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
    try:
        r = requests.get(f"{TRANSCRIBER_URL}/status", timeout=5)
        return r.json()
    except requests.RequestException as e:
        logger.error(f"Cannot reach transcriber: {e}")
        return None


def transcriber_start(title: str) -> dict | None:
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
# State Machine
# ---------------------------------------------------------------------------


class RecordingState:
    """Track the current state of meeting detection and recording."""

    IDLE = "idle"
    RECORDING = "recording"
    ERROR = "error"

    def __init__(self):
        self.state = self.IDLE
        self.meeting_title: str | None = None
        self.meeting_app: str | None = None  # "Zoom", "Teams", or "Manual"
        self.started_at: datetime.datetime | None = None
        self.auto_detected: bool = False

    def start(self, title: str, app: str, auto: bool = False):
        self.state = self.RECORDING
        self.meeting_title = title
        self.meeting_app = app
        self.started_at = datetime.datetime.now()
        self.auto_detected = auto

    def stop(self):
        self.state = self.IDLE
        self.meeting_title = None
        self.meeting_app = None
        self.started_at = None
        self.auto_detected = False

    def error(self):
        self.state = self.ERROR

    @property
    def duration(self) -> str:
        if not self.started_at:
            return "0:00"
        delta = datetime.datetime.now() - self.started_at
        minutes, seconds = divmod(int(delta.total_seconds()), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


# ---------------------------------------------------------------------------
# Menu Bar App
# ---------------------------------------------------------------------------


class MeetingBarApp(rumps.App):
    def __init__(self):
        super().__init__(
            name="Meeting Bar",
            title=ICON_IDLE,
            quit_button=None,  # We'll add our own quit button
        )
        self.state = RecordingState()
        self._detection_enabled = True
        self._lock = threading.Lock()

        # Pending UI updates from background threads
        self._pending_pilot_text: str | None = None
        self._pending_title_icon: str | None = None
        self._pending_status_text: str | None = None
        self._pending_start_enabled: bool | None = None
        self._pending_stop_enabled: bool | None = None
        self._pending_notification: tuple[str, str, str] | None = None
        self._error_clear_at: float | None = None

        # Menu items
        self.status_item = rumps.MenuItem("Status: Idle", callback=None)
        self.status_item.set_callback(None)

        self.start_item = rumps.MenuItem("Start Recording…", callback=self.on_start)
        self.stop_item = rumps.MenuItem("Stop Recording", callback=self.on_stop)
        self.stop_item.set_callback(None)  # Disabled initially

        self.auto_detect_item = rumps.MenuItem(
            "Auto-Detect Meetings",
            callback=self.on_toggle_detection,
        )
        self.auto_detect_item.state = 1  # Checked

        self.pilot_status_item = rumps.MenuItem("Pilot: checking…", callback=None)
        self.pilot_status_item.set_callback(None)

        self.log_item = rumps.MenuItem("View Log…", callback=self.on_view_log)

        self.quit_item = rumps.MenuItem("Quit", callback=self.on_quit)

        self.menu = [
            self.status_item,
            None,  # separator
            self.start_item,
            self.stop_item,
            None,  # separator
            self.auto_detect_item,
            self.pilot_status_item,
            self.log_item,
            None,  # separator
            self.quit_item,
        ]

        # Start background polling thread (avoids broken @rumps.timer on Python 3.14)
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    # -------------------------------------------------------------------
    # Background polling loop
    # -------------------------------------------------------------------

    def _poll_loop(self):
        """Background thread that polls every POLL_INTERVAL seconds."""
        time.sleep(2)  # Let the app finish starting
        logger.info("Detection loop active")
        while True:
            try:
                self._poll_work()
            except Exception as e:
                logger.error(f"Poll error: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL)

    def _sync_ui(self):
        """Apply pending UI updates. Called from _poll_work in background thread.
        
        rumps property setters (title, etc.) are thin wrappers around Cocoa
        properties. Direct access from a background thread is technically
        not ideal but works reliably for simple property mutations in rumps.
        """
        with self._lock:
            if self._pending_pilot_text is not None:
                self.pilot_status_item.title = self._pending_pilot_text
                self._pending_pilot_text = None

            if self._pending_title_icon is not None:
                self.title = self._pending_title_icon
                self._pending_title_icon = None

            if self._pending_status_text is not None:
                self.status_item.title = self._pending_status_text
                self._pending_status_text = None

            if self._pending_start_enabled is not None:
                if self._pending_start_enabled:
                    self.start_item.set_callback(self.on_start)
                else:
                    self.start_item.set_callback(None)
                self._pending_start_enabled = None

            if self._pending_stop_enabled is not None:
                if self._pending_stop_enabled:
                    self.stop_item.set_callback(self.on_stop)
                else:
                    self.stop_item.set_callback(None)
                self._pending_stop_enabled = None

            # Notifications must be on main thread — defer to next user interaction
            # rumps.notification from background thread generally works on macOS
            if self._pending_notification is not None:
                ntf_title, ntf_subtitle, ntf_message = self._pending_notification
                self._pending_notification = None
                try:
                    rumps.notification(title=ntf_title, subtitle=ntf_subtitle, message=ntf_message)
                except Exception:
                    logger.debug("Notification delivery failed (non-critical)")

            # Clear error state after timeout
            if self._error_clear_at and time.time() >= self._error_clear_at:
                self._error_clear_at = None
                if self.state.state == RecordingState.IDLE:
                    self.title = ICON_IDLE
                    self.status_item.title = "Status: Idle"
                    self.start_item.set_callback(self.on_start)
                    self.stop_item.set_callback(None)

    def _poll_work(self):
        """Background work for polling — runs in the poll thread."""
        # Update pilot status
        status = transcriber_status()
        if status:
            pilot_text = "Pilot: connected"
            if status.get("recording"):
                rec = status["recording"]
                pilot_text += f" — recording '{rec.get('title', '?')}'"
        else:
            pilot_text = "Pilot: unreachable"

        with self._lock:
            self._pending_pilot_text = pilot_text

        # Apply all pending UI updates
        self._sync_ui()

        # Meeting detection
        if not self._detection_enabled:
            return

        with self._lock:
            current_state = self.state.state
            was_auto = self.state.auto_detected

        meeting_app = detect_meeting()

        if current_state == RecordingState.IDLE and meeting_app:
            # Meeting detected — auto-start
            logger.info(f"Meeting detected: {meeting_app}")
            self._auto_start(meeting_app)

        elif current_state == RecordingState.RECORDING and was_auto and not meeting_app:
            # Meeting ended — auto-stop
            logger.info(f"Meeting ended (was: {self.state.meeting_app})")
            self._stop_recording()

    # -------------------------------------------------------------------
    # Recording control
    # -------------------------------------------------------------------

    def _start_recording(self, title: str, app: str, auto: bool = False):
        """Start the full recording pipeline. Call from background thread."""
        logger.info(f"Starting recording: '{title}' ({app}, auto={auto})")

        # Find audio device
        device_name, quality = find_best_device()
        if not device_name:
            logger.error("No suitable audio device found")
            self._set_error("No audio device")
            return False

        # Find mic for mixing
        mic_name = None
        if quality == "full":
            mic_name = find_mic_device()

        # Start VBAN sender
        try:
            start_sender(device_name, mic=mic_name)
        except Exception as e:
            logger.error(f"Failed to start VBAN sender: {e}")
            self._set_error("VBAN sender failed")
            return False

        # Wait for VBAN to connect
        time.sleep(3)

        # Start recording on pilot
        result = transcriber_start(title)
        if not result:
            logger.error("Failed to start recording on pilot")
            stop_sender()
            self._set_error("Pilot start failed")
            return False

        # Update state and queue UI update
        with self._lock:
            self.state.start(title, app, auto=auto)
            self._pending_title_icon = ICON_RECORDING
            self._pending_status_text = f"Recording: {title}"
            self._pending_start_enabled = False
            self._pending_stop_enabled = True

        logger.info(f"Recording started: '{title}'")
        return True

    def _stop_recording(self):
        """Stop the full recording pipeline. Call from background thread."""
        logger.info("Stopping recording")

        result = transcriber_stop()
        stop_sender()

        with self._lock:
            title = self.state.meeting_title
            duration = self.state.duration
            self.state.stop()

            self._pending_title_icon = ICON_IDLE
            self._pending_status_text = "Status: Idle"
            self._pending_start_enabled = True
            self._pending_stop_enabled = False

        if result:
            logger.info(f"Recording stopped: '{title}' ({duration})")
            with self._lock:
                self._pending_notification = (
                    "Recording Stopped",
                    title or "Meeting",
                    f"Duration: {duration}. Transcription queued.",
                )
        else:
            logger.warning("Recording stop — no active recording on pilot")

    def _auto_start(self, app: str):
        """Auto-start recording for a detected meeting."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        title = f"{app} Meeting {timestamp}"
        threading.Thread(
            target=self._start_recording,
            args=(title, app, True),
            daemon=True,
        ).start()

    def _set_error(self, msg: str):
        """Queue error state display. Clears after 10 seconds."""
        with self._lock:
            self._pending_title_icon = ICON_ERROR
            self._pending_status_text = f"Error: {msg}"
            self._pending_start_enabled = True
            self._pending_stop_enabled = False
            self._error_clear_at = time.time() + 10

    # -------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------

    def on_start(self, _sender):
        """Manual start — prompt for meeting title."""
        window = rumps.Window(
            title="Start Recording",
            message="Enter a meeting title:",
            default_text="",
            ok="Start",
            cancel="Cancel",
            dimensions=(320, 24),
        )
        response = window.run()
        if response.clicked:
            title = response.text.strip()
            if not title:
                title = f"Meeting {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
            threading.Thread(
                target=self._start_recording,
                args=(title, "Manual", False),
                daemon=True,
            ).start()

    def on_stop(self, _sender):
        """Manual stop."""
        with self._lock:
            # Immediately disable stop button to prevent double-clicks
            self._pending_stop_enabled = False
        threading.Thread(target=self._stop_recording, daemon=True).start()

    def on_toggle_detection(self, sender):
        """Toggle auto-detection on/off."""
        self._detection_enabled = not self._detection_enabled
        sender.state = 1 if self._detection_enabled else 0
        state = "enabled" if self._detection_enabled else "disabled"
        logger.info(f"Auto-detection {state}")

    def on_view_log(self, _sender):
        """Open the log file in Console.app."""
        subprocess.Popen(["open", "-a", "Console", str(LOG_PATH)])

    def on_quit(self, _sender):
        """Clean shutdown."""
        if self.state.state == RecordingState.RECORDING:
            # Stop recording before quitting
            logger.info("Quit requested — stopping recording first")
            self._stop_recording()
        rumps.quit_application()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    logger.info("Meeting Bar starting")
    logger.info(f"  Transcriber: {TRANSCRIBER_URL}")
    logger.info(f"  VBAN target: {PILOT_HOST}:{VBAN_PORT}")
    logger.info(f"  Poll interval: {POLL_INTERVAL}s")
    logger.info(f"  Log file: {LOG_PATH}")

    app = MeetingBarApp()
    app.run()


if __name__ == "__main__":
    main()
