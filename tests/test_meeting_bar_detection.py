#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest>=8.0.0",
#     "rumps>=0.4.0",
#     "requests>=2.31.0",
#     "sounddevice>=0.5.0",
#     "pyobjc>=12.0",
# ]
# ///
"""Tests for meeting_bar.py meeting detection helpers."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "transcriber"))
import meeting_bar


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def _audiomxd_output(*states: tuple[str, bool]) -> str:
    lines = []
    for session_id, is_recording in states:
        state = "true" if is_recording else "false"
        lines.append(
            f"{{ sessionID: {session_id}, sessionType: 'prim', isRecording: {state} }},"
        )
    return "\n".join(lines)


def test_audiomxd_end_detection_keeps_cached_true_session(monkeypatch):
    """Unrelated Teams false side sessions should not stop a live call."""
    meeting_bar._AUDIOMXD_SESSION_STATES.clear()
    outputs = iter(
        [
            _audiomxd_output(("0x224002", True)),
            _audiomxd_output(("0x224004", False)),
        ]
    )

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(next(outputs))

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert meeting_bar._audiomxd_session_active("Microsoft Teams") is True
    assert meeting_bar._audiomxd_session_active("Microsoft Teams") is True


def test_audiomxd_start_detection_ignores_cached_sessions(monkeypatch):
    """Start detection should not auto-start from an old cached true state."""
    meeting_bar._AUDIOMXD_SESSION_STATES.clear()
    meeting_bar._AUDIOMXD_SESSION_STATES["Microsoft Teams"] = {"0x224002": True}

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess("")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert (
        meeting_bar._audiomxd_session_active(
            "Microsoft Teams",
            default_if_no_entries=False,
            use_cached_sessions=False,
        )
        is False
    )


def test_audiomxd_same_session_false_ends_cached_session(monkeypatch):
    """The actual call session should become inactive when it reports false."""
    meeting_bar._AUDIOMXD_SESSION_STATES.clear()
    outputs = iter(
        [
            _audiomxd_output(("0x224002", True)),
            _audiomxd_output(("0x224002", False)),
        ]
    )

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(next(outputs))

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert meeting_bar._audiomxd_session_active("Microsoft Teams") is True
    assert meeting_bar._audiomxd_session_active("Microsoft Teams") is False
