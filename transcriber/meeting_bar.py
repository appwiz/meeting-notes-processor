#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "rumps>=0.4.0",
#     "requests>=2.31.0",
#     "sounddevice>=0.5.0",
# ]
# ///
"""
Meeting Bar — macOS menu bar app for automatic meeting recording.

Sits in the menu bar showing recording state. Detects Zoom/Teams meetings
and automatically starts/stops recording via the VBAN → pilot pipeline.

States:
  🎙  Idle
  🔴 Recording
  ⚠️  Error

Meeting detection:
  - Zoom: checks for CptHost subprocess (reliable in-meeting indicator)
  - Teams: two-tier detection because new Teams 2.x exposes no window
    titles and AVCaptureDevice doesn't see its mic usage:
    * Start: MSTeams process running + physical mic has active CoreAudio I/O
      (via compiled mic_active helper)
    * End: queries macOS audiomxd log for Teams audio session state, since
      our own VBAN sender keeps the mic active during recording

Usage:
  uv run meeting_bar.py
"""

import datetime
import logging
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

import requests
import rumps
import sounddevice as sd
from PyObjCTools.AppHelper import callAfter

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "http://pilot:8000")
PILOT_HOST = os.getenv("PILOT_HOST", "pilot")
VBAN_PORT = int(os.getenv("VBAN_PORT", "6980"))
POLL_INTERVAL = int(os.getenv("MEETING_POLL_INTERVAL", "5"))  # seconds

# Calendar org file for meeting title lookup (optional)
CALENDAR_ORG = os.getenv("MEETING_CALENDAR_ORG", os.path.expanduser("~/gtd/outlook.org"))

DEVICE_PREFERENCE = [
    "BlackHole 2ch",
    "ZoomAudioDevice",
    "Microsoft Teams",
]

VBAN_SEND_SCRIPT = Path(__file__).parent / "vban" / "vban_send.py"
PID_FILE = Path(os.getenv("MEETING_PID_FILE", "/tmp/meeting-vban-sender.pid"))
LOG_FILE = Path(os.getenv("MEETING_LOG_FILE", "/tmp/meeting-vban-sender.log"))

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

ICON_IDLE = "🎙"
ICON_RECORDING = "🔴"
ICON_ERROR = "⚠️"

# Compiled Swift helper for CoreAudio mic detection (Teams 2.x)
MIC_ACTIVE_BIN = Path(__file__).parent / "mic_active"


# ---------------------------------------------------------------------------
# Calendar Title Lookup
# ---------------------------------------------------------------------------


def lookup_calendar_title(now: datetime.datetime | None = None) -> str | None:
    """Find the best matching calendar entry title for the current time.

    Parses CALENDAR_ORG (org-mode format) and finds the meeting whose start
    time is closest to `now`, subject to these rules:
      - Nothing ever starts more than 5 minutes early
      - If we're more than 25 minutes past a meeting's start time, it's
        likely a spontaneous meeting (return None)
      - Only considers today's entries

    Returns the meeting title string, or None if no match.
    """
    cal_path = Path(CALENDAR_ORG)
    if not cal_path.exists():
        return None

    if now is None:
        now = datetime.datetime.now()

    today_str = now.strftime("%Y-%m-%d")

    try:
        content = cal_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.debug(f"Cannot read calendar file: {e}")
        return None

    # Parse org entries: * Title <YYYY-MM-DD Day HH:MM-HH:MM>
    entry_re = re.compile(
        r'^\* (.+?) <(\d{4}-\d{2}-\d{2}) \w{3} (\d{2}:\d{2})-(\d{2}:\d{2})>',
        re.MULTILINE,
    )

    best_title = None
    best_delta = None  # seconds from meeting start to now (positive = we're late)

    for m in entry_re.finditer(content):
        date_str = m.group(2)
        if date_str != today_str:
            continue

        title = m.group(1).strip()
        start_str = m.group(3)

        try:
            start_time = datetime.datetime.strptime(
                f"{date_str} {start_str}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            continue

        delta_s = (now - start_time).total_seconds()

        # Skip if meeting hasn't started yet and we're more than 5 min early
        if delta_s < -300:
            continue
        # Skip if we're more than 25 min past the start
        if delta_s > 1500:
            continue

        abs_delta = abs(delta_s)
        if best_delta is None or abs_delta < best_delta:
            best_delta = abs_delta
            best_title = title

    return best_title

# ---------------------------------------------------------------------------
# Meeting Detection
# ---------------------------------------------------------------------------


def detect_zoom_meeting() -> bool:
    """Check for CptHost process (only runs during active Zoom meetings)."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", "CptHost"], capture_output=True, timeout=3,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _physical_mic_active() -> bool:
    """Check if any physical microphone has active CoreAudio I/O.

    Calls the compiled mic_active helper (Swift/CoreAudio) which checks
    kAudioDevicePropertyDeviceIsRunningSomewhere on physical input devices,
    ignoring virtual devices (BlackHole, ZoomAudioDevice, Teams Audio, etc.).

    AVCaptureDevice.isInUseByAnotherApplication() does NOT work for Teams 2.x
    because Teams uses CoreAudio directly, not AVCaptureDevice.
    """
    if not MIC_ACTIVE_BIN.exists():
        logger.warning(f"mic_active binary not found at {MIC_ACTIVE_BIN}")
        return False
    try:
        result = subprocess.run(
            [str(MIC_ACTIVE_BIN)], capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() == "YES"
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug(f"mic_active check failed: {e}")
        return False


def detect_teams_meeting() -> bool:
    """Check if Teams is in an active call (for START detection).

    New Teams (2.x) doesn't expose meeting titles via CGWindowListCopyWindowInfo
    (all titles are empty) and maintains hundreds of UDP sockets even when idle.
    AVCaptureDevice.isInUseByAnotherApplication() also misses Teams calls because
    Teams uses CoreAudio directly.

    Instead, we detect active calls by checking two conditions:
      1. MSTeams process is running
      2. A physical microphone has active CoreAudio I/O (via mic_active helper)

    WARNING: This check is only reliable for START detection. Once our VBAN
    sender is running, it keeps the mic active, so this always returns True.
    For END detection while recording, use _teams_audio_session_active() instead.
    """
    try:
        # Check if MSTeams is running
        result = subprocess.run(
            ["pgrep", "-x", "MSTeams"], capture_output=True, timeout=3,
        )
        if result.returncode != 0:
            return False
        # Check if a physical microphone has active I/O
        return _physical_mic_active()
    except (subprocess.TimeoutExpired, OSError):
        return False


def _teams_audio_session_active() -> bool:
    """Check if Teams has an active audio session via macOS audiomxd logs.

    This queries the system log for the most recent Teams audio recording state.
    The audiomxd daemon reliably logs 'isRecording: true/false' whenever Teams
    starts or stops an audio session (call join/leave).

    This is the only reliable signal for Teams meeting END detection because:
      - mic_active always returns YES while our VBAN sender is running
      - UDP socket counts are noisy (170+ even when idle)
      - Window titles are all empty in Teams 2.x
      - AVCaptureDevice doesn't see Teams mic usage

    Takes ~1.5s to run, acceptable for 5s polling interval.
    """
    try:
        result = subprocess.run(
            ["log", "show", "--last", "120s",
             "--predicate", 'process == "audiomxd" AND eventMessage CONTAINS "MSTeams" AND eventMessage CONTAINS "isRecording"',
             "--style", "compact"],
            capture_output=True, text=True, timeout=10,
        )
        # Find the last isRecording state
        lines = result.stdout.strip().splitlines()
        for line in reversed(lines):
            if "isRecording: true" in line:
                return True
            if "isRecording: false" in line:
                return False
        # No log entries found — assume still active (conservative)
        # This handles the case where the call has been going for >120s
        # without an audio state change.
        return True
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug(f"audiomxd log check failed: {e}")
        return True  # Fail-open: assume still active


def detect_meeting() -> str | None:
    """Returns "Zoom", "Teams", or None."""
    if detect_zoom_meeting():
        return "Zoom"
    if detect_teams_meeting():
        return "Teams"
    return None


# ---------------------------------------------------------------------------
# Audio Device Discovery
# ---------------------------------------------------------------------------


def find_best_device() -> tuple[str | None, str | None]:
    devices = sd.query_devices()
    available = {d["name"]: d for d in devices if d["max_input_channels"] > 0}
    for pref in DEVICE_PREFERENCE:
        for name in available:
            if pref.lower() in name.lower():
                quality = "full" if "blackhole" in name.lower() else "partial"
                return name, quality
    return None, None


def find_mic_device() -> str | None:
    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    if default_idx is not None and default_idx >= 0:
        d = devices[default_idx]
        if d["max_input_channels"] > 0 and not any(
            s in d["name"].lower() for s in ["blackhole", "zoom", "teams"]
        ):
            return d["name"]
    for d in devices:
        if d["max_input_channels"] > 0 and not any(
            s in d["name"].lower() for s in ["blackhole", "zoom", "teams"]
        ):
            return d["name"]
    return None


# ---------------------------------------------------------------------------
# VBAN Sender Management
# ---------------------------------------------------------------------------


def _sender_running() -> int | None:
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
    existing = _sender_running()
    if existing:
        logger.info(f"VBAN sender already running (PID {existing})")
        return existing
    cmd = ["uv", "run", str(VBAN_SEND_SCRIPT), "-d", device, "-t", PILOT_HOST, "-p", str(VBAN_PORT)]
    if mic:
        cmd.extend(["--mic", mic])
    log_fh = open(LOG_FILE, "w")
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, start_new_session=True)
    PID_FILE.write_text(str(proc.pid))
    mode = f"mixed ({device} + {mic})" if mic else device
    logger.info(f"VBAN sender started (PID {proc.pid}) → {mode}")
    return proc.pid


def stop_sender():
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


# ---------------------------------------------------------------------------
# Transcriber API
# ---------------------------------------------------------------------------


def transcriber_status() -> dict | None:
    try:
        return requests.get(f"{TRANSCRIBER_URL}/status", timeout=5).json()
    except requests.RequestException as e:
        logger.error(f"Cannot reach transcriber: {e}")
        return None


def transcriber_start(title: str) -> dict | None:
    try:
        r = requests.post(f"{TRANSCRIBER_URL}/start", json={"title": title}, timeout=10)
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
            return None
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.error(f"Failed to stop recording: {e}")
        return None


# ---------------------------------------------------------------------------
# Menu Bar App
#
# THREADING MODEL:
#   - Main thread: NSApplication run loop. All Cocoa UI access here only.
#   - Poll thread: network I/O + meeting detection. Sets Python-only flags.
#   - Recording threads: short-lived start/stop. Sets Python-only flags.
#
# RULE: Background threads NEVER touch Cocoa objects (no set_callback,
# no .title=, no rumps.alert). They only set Python variables under _lock.
# The main thread reads those variables when the user opens the menu.
# ---------------------------------------------------------------------------


class MeetingBarApp(rumps.App):
    def __init__(self):
        super().__init__(name="Meeting Bar", title=ICON_IDLE, quit_button=None)

        self._lock = threading.Lock()
        self._recording = False
        self._recording_title: str | None = None
        self._recording_app: str | None = None
        self._recording_auto: bool = False
        self._started_at: datetime.datetime | None = None
        self._detection_enabled = True
        self._busy = False  # True while start/stop in progress
        self._suppress_auto = False  # True after manual stop of auto-started recording
        self._pilot_text = "Pilot: checking…"

        # Menu — all items always have callbacks via @rumps.clicked.
        # Keep refs so we can update visibility via callAfter.
        self._status_item = rumps.MenuItem("Status: Idle")
        self._start_item = rumps.MenuItem("Start Recording")
        self._stop_item = rumps.MenuItem("Stop Recording")
        self._pilot_item = rumps.MenuItem("Pilot: checking…")
        self.menu = [
            self._status_item,
            None,
            self._start_item,
            self._stop_item,
            None,
            rumps.MenuItem("Auto-Detect Meetings"),
            self._pilot_item,
            rumps.MenuItem("View Log…"),
            None,
            rumps.MenuItem("Quit Meeting Bar"),
        ]
        self.menu["Auto-Detect Meetings"].state = 1

        # Initial UI state: hide Stop Recording
        self._stop_item.hidden = True

        # Start background polling
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # -------------------------------------------------------------------
    # Helpers (main thread only)
    # -------------------------------------------------------------------

    @property
    def _duration(self) -> str:
        if not self._started_at:
            return "0:00"
        delta = datetime.datetime.now() - self._started_at
        m, s = divmod(int(delta.total_seconds()), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # -------------------------------------------------------------------
    # Main-thread UI sync via callAfter
    # -------------------------------------------------------------------

    def _schedule_ui_update(self):
        """Schedule a UI state sync on the main thread."""
        try:
            callAfter(self._apply_ui_state)
        except Exception as e:
            logger.debug(f"callAfter UI update failed: {e}")

    def _apply_ui_state(self):
        """Apply current state to all UI elements. Runs on main thread."""
        try:
            with self._lock:
                recording = self._recording
                busy = self._busy
                rec_title = self._recording_title
                pilot = self._pilot_text

            # Icon
            if recording:
                self.title = ICON_RECORDING
            elif busy:
                self.title = "⏳"
            else:
                self.title = ICON_IDLE

            # Status text
            if recording:
                self._status_item.title = f"Recording: {rec_title} ({self._duration})"
            elif busy:
                self._status_item.title = "Starting…"
            else:
                self._status_item.title = "Status: Idle"

            # Show/hide Start and Stop mutually exclusively
            self._start_item.hidden = recording or busy
            self._stop_item.hidden = not recording

            # Pilot status
            self._pilot_item.title = pilot
        except Exception as e:
            logger.error(f"_apply_ui_state error: {e}", exc_info=True)

    # -------------------------------------------------------------------
    # Background polling
    # -------------------------------------------------------------------

    def _poll_loop(self):
        time.sleep(2)
        logger.info("Detection loop active")
        while True:
            try:
                self._poll_work()
            except Exception as e:
                logger.error(f"Poll error: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL)

    def _poll_work(self):
        # Pilot status (network I/O)
        status = transcriber_status()
        pilot_text = "Pilot: connected" if status else "Pilot: unreachable"
        if status and status.get("recording"):
            pilot_text += f" — rec '{status['recording'].get('title', '?')}'"

        with self._lock:
            self._pilot_text = pilot_text

        # Schedule full UI update on main thread
        self._schedule_ui_update()

        # Meeting detection
        if not self._detection_enabled or self._busy:
            return

        meeting_app = detect_meeting()

        with self._lock:
            is_recording = self._recording
            is_auto = self._recording_auto
            rec_app = self._recording_app

        if not is_recording and meeting_app:
            if self._suppress_auto:
                pass  # User manually stopped; wait for meeting to end
            else:
                logger.info(f"Meeting detected: {meeting_app}")
                cal_title = lookup_calendar_title()
                if cal_title:
                    title = cal_title
                    logger.info(f"Calendar match: '{cal_title}'")
                else:
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
                    title = f"{meeting_app} Meeting {timestamp}"
                threading.Thread(
                    target=self._do_start, args=(title, meeting_app, True), daemon=True,
                ).start()

        elif is_recording and is_auto:
            # End detection is app-specific because our VBAN sender keeps
            # the physical mic active, making generic detect_meeting()
            # unreliable (it would see "Teams" even during a Zoom recording).
            if rec_app == "Zoom":
                still_active = detect_zoom_meeting()
            elif rec_app == "Teams":
                still_active = _teams_audio_session_active()
            else:
                still_active = meeting_app is not None

            if not still_active:
                logger.info(f"Meeting ended (was: {rec_app})")
                threading.Thread(target=self._do_stop, daemon=True).start()

        elif not meeting_app and self._suppress_auto:
            logger.info("Meeting ended, clearing auto-suppress")
            self._suppress_auto = False

    # -------------------------------------------------------------------
    # Recording pipeline (background threads)
    # -------------------------------------------------------------------

    def _do_start(self, title: str, app: str, auto: bool = False):
        with self._lock:
            if self._recording or self._busy:
                return
            self._busy = True

        try:
            logger.info(f"Starting recording: '{title}' ({app}, auto={auto})")

            device_name, quality = find_best_device()
            if not device_name:
                logger.error("No suitable audio device found")
                return

            mic_name = find_mic_device() if quality == "full" else None
            start_sender(device_name, mic=mic_name)
            time.sleep(3)

            result = transcriber_start(title)
            if not result:
                logger.error("Failed to start recording on pilot")
                stop_sender()
                return

            with self._lock:
                self._recording = True
                self._recording_title = title
                self._recording_app = app
                self._recording_auto = auto
                self._started_at = datetime.datetime.now()

            self._schedule_ui_update()
            logger.info(f"Recording started: '{title}'")

        except Exception as e:
            logger.error(f"Start failed: {e}", exc_info=True)
        finally:
            with self._lock:
                self._busy = False
            self._schedule_ui_update()

    def _do_stop(self):
        with self._lock:
            if not self._recording:
                return
            self._busy = True

        try:
            logger.info("Stopping recording")
            result = transcriber_stop()
            stop_sender()

            with self._lock:
                title = self._recording_title
                duration = self._duration
                self._recording = False
                self._recording_title = None
                self._recording_app = None
                self._recording_auto = False
                self._started_at = None

            self._schedule_ui_update()
            if result:
                logger.info(f"Recording stopped: '{title}' ({duration})")
            else:
                logger.warning("No active recording on pilot")

        except Exception as e:
            logger.error(f"Stop failed: {e}", exc_info=True)
            with self._lock:
                self._recording = False
                self._recording_title = None
        finally:
            with self._lock:
                self._busy = False
            self._schedule_ui_update()

    # -------------------------------------------------------------------
    # Menu callbacks — @rumps.clicked runs on main thread
    # -------------------------------------------------------------------

    @rumps.clicked("Start Recording")
    def on_start(self, sender):
        try:
            logger.info("on_start callback fired")
            with self._lock:
                if self._recording or self._busy:
                    logger.info("Already recording or busy")
                    return

            cal_title = lookup_calendar_title()
            if cal_title:
                title = cal_title
                logger.info(f"Manual start with calendar title: '{title}'")
            else:
                title = f"Meeting at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
                logger.info(f"Manual start: '{title}'")
            self._schedule_ui_update()
            threading.Thread(
                target=self._do_start, args=(title, "Manual", False), daemon=True,
            ).start()
        except Exception as e:
            logger.error(f"on_start error: {e}", exc_info=True)

    @rumps.clicked("Stop Recording")
    def on_stop(self, sender):
        try:
            logger.info("on_stop callback fired")
            with self._lock:
                if not self._recording:
                    rumps.notification("Meeting Bar", "Not Recording",
                                      "No recording in progress.")
                    return
                was_auto = self._recording_auto
            # If meeting is still active, suppress auto-restart
            if was_auto and detect_meeting():
                self._suppress_auto = True
                logger.info("Suppressing auto-restart until meeting ends")
            threading.Thread(target=self._do_stop, daemon=True).start()
        except Exception as e:
            logger.error(f"on_stop error: {e}", exc_info=True)

    @rumps.clicked("Auto-Detect Meetings")
    def on_toggle_detection(self, sender):
        try:
            self._detection_enabled = not self._detection_enabled
            sender.state = 1 if self._detection_enabled else 0
            logger.info(f"Auto-detection {'enabled' if self._detection_enabled else 'disabled'}")
        except Exception as e:
            logger.error(f"on_toggle_detection error: {e}", exc_info=True)

    @rumps.clicked("View Log…")
    def on_view_log(self, sender):
        try:
            subprocess.Popen(["open", "-a", "Console", str(LOG_PATH)])
        except Exception as e:
            logger.error(f"on_view_log error: {e}", exc_info=True)

    @rumps.clicked("Quit Meeting Bar")
    def on_quit(self, sender):
        try:
            logger.info("Quit requested")
            if self._recording:
                logger.info("Stopping recording before quit")
                self._do_stop()
            logger.info("Meeting Bar exiting")
        except Exception as e:
            logger.error(f"on_quit error: {e}", exc_info=True)
        os._exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    logger.info("Meeting Bar starting")
    logger.info(f"  Transcriber: {TRANSCRIBER_URL}")
    logger.info(f"  VBAN target: {PILOT_HOST}:{VBAN_PORT}")
    logger.info(f"  Poll interval: {POLL_INTERVAL}s")
    logger.info(f"  Calendar: {CALENDAR_ORG}")
    logger.info(f"  Log: {LOG_PATH}")
    logger.info("  Quit: use Quit menu item, or Ctrl-\\ from terminal")

    app = MeetingBarApp()
    app.run()


if __name__ == "__main__":
    main()
