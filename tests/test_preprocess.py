#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest>=8.0.0",
#     "pyyaml>=6.0.0",
# ]
# ///
"""
Tests for transcript pre-processing: junk filter and multi-meeting split.

Run with: uv run --with pytest --with pyyaml pytest tests/test_preprocess.py -v
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import run_summarization


def _write_temp(content: str, dir: str = None) -> str:
    """Write content to a temp file and return its path."""
    f = tempfile.NamedTemporaryFile(
        mode='w', suffix='.txt', delete=False, dir=dir
    )
    f.write(content)
    f.close()
    return f.name


YAML_HEADER = (
    "---\n"
    "meeting_start: 2026-02-18T14:00:00-08:00\n"
    "meeting_end: 2026-02-18T14:35:00-08:00\n"
    "recording_source: transcriber\n"
    "---\n\n"
)

SHORT_HEADER = (
    "---\n"
    "meeting_start: 2026-02-18T14:42:30-08:00\n"
    "meeting_end: 2026-02-18T14:42:55-08:00\n"
    "recording_source: transcriber\n"
    "---\n\n"
)


class TestIsTranscriptWorthProcessing:
    """Tests for the junk transcript filter."""

    def test_short_body_rejected(self):
        path = _write_temp(YAML_HEADER + "Okay. Mm-hmm.")
        try:
            ok, reason = run_summarization.is_transcript_worth_processing(path)
            assert not ok
            assert "too short" in reason
        finally:
            os.unlink(path)

    def test_brief_duration_rejected(self):
        """Body > 200 chars but duration < 60s should be rejected."""
        path = _write_temp(SHORT_HEADER + "x" * 250)
        try:
            ok, reason = run_summarization.is_transcript_worth_processing(path)
            assert not ok
            assert "too brief" in reason
        finally:
            os.unlink(path)

    def test_normal_transcript_accepted(self):
        body = "Discussion about project planning and timeline. " * 20
        path = _write_temp(YAML_HEADER + body)
        try:
            ok, reason = run_summarization.is_transcript_worth_processing(path)
            assert ok
            assert reason == ""
        finally:
            os.unlink(path)

    def test_no_header_short_body_rejected(self):
        path = _write_temp("Okay. Thanks.")
        try:
            ok, reason = run_summarization.is_transcript_worth_processing(path)
            assert not ok
            assert "too short" in reason
        finally:
            os.unlink(path)

    def test_no_header_long_body_accepted(self):
        """Without YAML header, long enough body should pass (no duration check possible)."""
        body = "Discussion about project planning. " * 20
        path = _write_temp(body)
        try:
            ok, reason = run_summarization.is_transcript_worth_processing(path)
            assert ok
        finally:
            os.unlink(path)

    def test_empty_file_rejected(self):
        path = _write_temp("")
        try:
            ok, reason = run_summarization.is_transcript_worth_processing(path)
            assert not ok
        finally:
            os.unlink(path)


class TestSplitTranscript:
    """Tests for splitting a transcript at given positions."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.inbox = os.path.join(self.tmpdir, "inbox")
        os.makedirs(self.inbox)
        self.paths = {
            'workspace': self.tmpdir,
            'inbox': self.inbox,
            'transcripts': os.path.join(self.tmpdir, 'transcripts'),
            'notes': os.path.join(self.tmpdir, 'notes'),
        }

    def test_split_creates_two_parts(self):
        body_a = "First meeting content. " * 100
        body_b = "Second meeting content. " * 100
        full = body_a + body_b
        split_pos = len(body_a)

        filepath = os.path.join(self.inbox, "test-meeting.txt")
        with open(filepath, 'w') as f:
            f.write(full)

        new_files = run_summarization.split_transcript(filepath, [split_pos], self.paths)

        assert len(new_files) == 2
        assert "part1" in new_files[0]
        assert "part2" in new_files[1]
        assert not os.path.exists(filepath)  # original removed

        with open(new_files[0]) as f:
            content1 = f.read()
        with open(new_files[1]) as f:
            content2 = f.read()

        assert "First meeting content" in content1
        assert "Second meeting content" in content2

    def test_split_with_yaml_header_interpolates_timestamps(self):
        header = (
            "---\n"
            "meeting_start: 2026-01-08T10:00:00-08:00\n"
            "meeting_end: 2026-01-08T11:00:00-08:00\n"
            "recording_source: transcriber\n"
            "---\n\n"
        )
        body = "A" * 500 + "B" * 500
        filepath = os.path.join(self.inbox, "timed-meeting.txt")
        with open(filepath, 'w') as f:
            f.write(header + body)

        new_files = run_summarization.split_transcript(filepath, [500], self.paths)

        assert len(new_files) == 2
        with open(new_files[0]) as f:
            content1 = f.read()
        with open(new_files[1]) as f:
            content2 = f.read()

        # Part 1 should start at 10:00
        assert "10:00:00" in content1
        # Part 2 should start at ~10:30 and end at 11:00
        assert "11:00:00" in content2

    def test_split_empty_segment_skipped(self):
        """If a split position is at the very start or end, empty segments are skipped."""
        body = "Some content here."
        filepath = os.path.join(self.inbox, "edge-case.txt")
        with open(filepath, 'w') as f:
            f.write(body)

        # Split at position 0 — first segment would be empty
        new_files = run_summarization.split_transcript(filepath, [0], self.paths)
        assert len(new_files) == 1  # only one non-empty part


class TestExtractJsonObject:
    """Tests for the JSON extraction helper."""

    def test_simple_json(self):
        result = run_summarization._extract_json_object('Here is {"a": 1} the end')
        assert result == {"a": 1}

    def test_nested_json(self):
        result = run_summarization._extract_json_object('{"a": {"b": {"c": 3}}}')
        assert result == {"a": {"b": {"c": 3}}}

    def test_json_with_surrounding_text(self):
        result = run_summarization._extract_json_object(
            'Some preamble\n{"meeting_count": 2, "confidence": 0.9}\nDone'
        )
        assert result["meeting_count"] == 2

    def test_no_json(self):
        assert run_summarization._extract_json_object("no json here") is None

    def test_malformed_json(self):
        assert run_summarization._extract_json_object("{bad json}") is None
