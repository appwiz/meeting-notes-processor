#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest>=8.0.0",
# ]
# ///
"""
Tests for calendar integration in run_summarization.py

Covers:
- parse_calendar_org(): parsing example calendar.org data
- format_calendar_for_prompt(): formatting entries for LLM prompt
- parse_notes_org_for_calendar(): extracting metadata from generated notes
- time_overlaps(): matching meeting times to calendar entries
- enrich_with_calendar(): full enrichment pipeline (LLM mocked)
- Edge cases: no calendar, no participants, all-day events, multiple meetings

Uses example calendar data in examples/calendar.org.

Run with: uv run pytest tests/test_calendar.py -v
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import run_summarization

EXAMPLES_DIR = str(Path(__file__).parent.parent / 'examples')
EXAMPLE_CALENDAR = os.path.join(EXAMPLES_DIR, 'calendar.org')


# ============================================================================
# parse_calendar_org()
# ============================================================================

class TestParseCalendarOrg:
    """Tests for parse_calendar_org() with example calendar data."""

    def test_parses_all_entries(self):
        """Should parse all calendar entries from example data."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        assert len(entries) == 8

    def test_extracts_title(self):
        """Should extract meeting titles correctly."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        titles = [e['title'] for e in entries]
        assert 'Edd / Sarah 1:1' in titles
        assert 'Engineering Town Hall' in titles
        assert 'CIP SLT Sync' in titles

    def test_extracts_date(self):
        """Should extract dates in YYYY-MM-DD format."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        monday_entries = [e for e in entries if e['date'] == '2026-01-26']
        assert len(monday_entries) == 5

    def test_extracts_time_range(self):
        """Should extract start and end times."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        sarah = next(e for e in entries if e['title'] == 'Edd / Sarah 1:1')
        assert sarah['start_time'] == '09:00'
        assert sarah['end_time'] == '09:30'

    def test_all_day_event_has_no_times(self):
        """All-day events should have None for start/end times."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        all_hands = next(e for e in entries if e['title'] == 'Company All Hands')
        assert all_hands['start_time'] is None
        assert all_hands['end_time'] is None

    def test_extracts_participants(self):
        """Should extract participant names, stripping email addresses."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        sarah = next(e for e in entries if e['title'] == 'Edd / Sarah 1:1')
        assert 'Sarah Chen' in sarah['participants']
        assert 'Edd Wilder-James' in sarah['participants']
        # Email should be stripped
        assert not any('<' in p for p in sarah['participants'])

    def test_extracts_meeting_links(self):
        """Should extract video call links."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        sarah = next(e for e in entries if e['title'] == 'Edd / Sarah 1:1')
        assert len(sarah['meeting_links']) == 1
        assert 'teams.microsoft.com' in sarah['meeting_links'][0]

    def test_entry_without_participants(self):
        """Entries without PARTICIPANTS property should have empty list."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        lunch = next(e for e in entries if e['title'] == 'Lunch Break')
        assert lunch['participants'] == []

    def test_entry_without_meeting_link(self):
        """Entries without meeting links should have empty list."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        slt = next(e for e in entries if e['title'] == 'CIP SLT Sync')
        assert slt['meeting_links'] == []

    def test_multiple_participants(self):
        """Should handle entries with many participants."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        town_hall = next(e for e in entries if e['title'] == 'Engineering Town Hall')
        assert len(town_hall['participants']) == 6

    def test_empty_calendar_file(self):
        """Should return empty list for empty calendar file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.org', delete=False) as f:
            f.write('')
            f.flush()
            try:
                entries = run_summarization.parse_calendar_org(f.name)
                assert entries == []
            finally:
                os.unlink(f.name)

    def test_calendar_with_no_matching_entries(self):
        """Should parse entries even if they don't match any date we care about."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        tuesday_entries = [e for e in entries if e['date'] == '2026-01-27']
        assert len(tuesday_entries) == 3


# ============================================================================
# format_calendar_for_prompt()
# ============================================================================

class TestFormatCalendarForPrompt:
    """Tests for format_calendar_for_prompt()."""

    def test_formats_timed_entries(self):
        """Should format entries with time ranges and participants."""
        entries = [{'title': 'Edd / Sarah 1:1', 'date': '2026-01-26',
                    'start_time': '09:00', 'end_time': '09:30',
                    'participants': ['Sarah Chen', 'Edd Wilder-James'],
                    'meeting_links': ['https://teams.example.com/abc']}]
        
        result = run_summarization.format_calendar_for_prompt(entries, '2026-01-26')
        
        assert '1. [09:00-09:30] Edd / Sarah 1:1' in result
        assert 'Participants: Sarah Chen, Edd Wilder-James' in result
        assert 'Meeting link: https://teams.example.com/abc' in result

    def test_formats_all_day_events(self):
        """Should show 'all-day' for events without times."""
        entries = [{'title': 'Company All Hands', 'date': '2026-01-27',
                    'start_time': None, 'end_time': None,
                    'participants': [], 'meeting_links': []}]
        
        result = run_summarization.format_calendar_for_prompt(entries, '2026-01-27')
        
        assert '[all-day]' in result
        assert 'unknown' in result  # No participants

    def test_empty_entries(self):
        """Should return 'no entries' message for empty list."""
        result = run_summarization.format_calendar_for_prompt([], '2026-01-26')
        assert 'No calendar entries' in result

    def test_numbers_entries_sequentially(self):
        """Should number multiple entries starting from 1."""
        entries = [
            {'title': 'Meeting A', 'date': '2026-01-26',
             'start_time': '09:00', 'end_time': '10:00',
             'participants': ['Alice'], 'meeting_links': []},
            {'title': 'Meeting B', 'date': '2026-01-26',
             'start_time': '11:00', 'end_time': '12:00',
             'participants': ['Bob'], 'meeting_links': []},
        ]
        
        result = run_summarization.format_calendar_for_prompt(entries, '2026-01-26')
        
        assert '1. [09:00-10:00] Meeting A' in result
        assert '2. [11:00-12:00] Meeting B' in result


# ============================================================================
# parse_notes_org_for_calendar()
# ============================================================================

class TestParseNotesOrgForCalendar:
    """Tests for parse_notes_org_for_calendar()."""

    def test_extracts_title(self):
        """Should extract the meeting title from the heading."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.org', delete=False) as f:
            f.write("** Q1 Planning Discussion :note:transcribed:\n"
                    "[2026-01-26 Mon 09:00]\n"
                    ":PROPERTIES:\n"
                    ":PARTICIPANTS: Sarah, Edd\n"
                    ":SLUG: q1-planning\n"
                    ":END:\n\nTL;DR: stuff\n")
            f.flush()
            try:
                result = run_summarization.parse_notes_org_for_calendar(f.name)
                assert result['title'] == 'Q1 Planning Discussion'
            finally:
                os.unlink(f.name)

    def test_extracts_date_and_time(self):
        """Should extract date and time from timestamp."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.org', delete=False) as f:
            f.write("** Test :note:transcribed:\n"
                    "[2026-01-26 Mon 14:30]\n"
                    ":PROPERTIES:\n:SLUG: test\n:END:\n")
            f.flush()
            try:
                result = run_summarization.parse_notes_org_for_calendar(f.name)
                assert result['date'] == '2026-01-26'
                assert result['time'] == '14:30'
            finally:
                os.unlink(f.name)

    def test_extracts_participants(self):
        """Should extract and split participant list."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.org', delete=False) as f:
            f.write("** Test :note:transcribed:\n"
                    "[2026-01-26 Mon]\n"
                    ":PROPERTIES:\n"
                    ":PARTICIPANTS: Sarah Chen, Edd Wilder-James, Mia Arts\n"
                    ":SLUG: test\n:END:\n")
            f.flush()
            try:
                result = run_summarization.parse_notes_org_for_calendar(f.name)
                assert len(result['participants']) == 3
                assert 'Sarah Chen' in result['participants']
            finally:
                os.unlink(f.name)

    def test_extracts_slug_and_topic(self):
        """Should extract SLUG and TOPIC properties."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.org', delete=False) as f:
            f.write("** Test :note:transcribed:\n"
                    "[2026-01-26 Mon]\n"
                    ":PROPERTIES:\n"
                    ":PARTICIPANTS: Alice\n"
                    ":SLUG: q1-planning\n"
                    ":TOPIC: Q1 Planning\n"
                    ":END:\n")
            f.flush()
            try:
                result = run_summarization.parse_notes_org_for_calendar(f.name)
                assert result['slug'] == 'q1-planning'
                assert result['topic'] == 'Q1 Planning'
            finally:
                os.unlink(f.name)

    def test_handles_missing_properties(self):
        """Should handle notes without PARTICIPANTS or TOPIC."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.org', delete=False) as f:
            f.write("** Test :note:transcribed:\n"
                    "[2026-01-26 Mon 10:00]\n"
                    ":PROPERTIES:\n:SLUG: test\n:END:\n")
            f.flush()
            try:
                result = run_summarization.parse_notes_org_for_calendar(f.name)
                assert result['participants'] == []
                assert result['topic'] is None
            finally:
                os.unlink(f.name)

    def test_handles_date_without_time(self):
        """Should handle timestamp with date but no time."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.org', delete=False) as f:
            f.write("** Test :note:transcribed:\n"
                    "[2026-01-26 Mon]\n"
                    ":PROPERTIES:\n:SLUG: test\n:END:\n")
            f.flush()
            try:
                result = run_summarization.parse_notes_org_for_calendar(f.name)
                assert result['date'] == '2026-01-26'
                assert result['time'] is None
            finally:
                os.unlink(f.name)


# ============================================================================
# time_overlaps() — additional tests with calendar data
# ============================================================================

class TestTimeOverlapsWithCalendar:
    """Additional time_overlaps() tests exercising calendar entry patterns."""

    def test_one_on_one_exact_match(self):
        """1:1 at 09:00-09:30 should match meeting starting at 09:00."""
        cal = {'start_time': '09:00', 'end_time': '09:30'}
        start = datetime(2026, 1, 26, 9, 0)
        end = datetime(2026, 1, 26, 9, 35)
        assert run_summarization.time_overlaps(cal, start, end) is True

    def test_meeting_runs_over(self):
        """Meeting running over calendar slot should still overlap."""
        cal = {'start_time': '14:00', 'end_time': '14:30'}
        start = datetime(2026, 1, 26, 14, 0)
        end = datetime(2026, 1, 26, 14, 55)
        assert run_summarization.time_overlaps(cal, start, end) is True

    def test_meeting_starts_early(self):
        """Meeting starting a few minutes before slot should overlap."""
        cal = {'start_time': '15:00', 'end_time': '16:00'}
        start = datetime(2026, 1, 26, 14, 56)
        end = datetime(2026, 1, 26, 16, 5)
        assert run_summarization.time_overlaps(cal, start, end) is True

    def test_adjacent_slots_dont_overlap(self):
        """Back-to-back meetings should not overlap each other."""
        cal = {'start_time': '10:00', 'end_time': '11:00'}
        # Meeting runs 11:05 to 12:00 — should not match 10:00-11:00
        start = datetime(2026, 1, 26, 11, 10)
        end = datetime(2026, 1, 26, 12, 0)
        assert run_summarization.time_overlaps(cal, start, end) is False

    def test_all_day_event_overlaps_anything(self):
        """All-day calendar entries should match any meeting time."""
        cal = {'start_time': None, 'end_time': None}
        start = datetime(2026, 1, 27, 16, 0)
        end = datetime(2026, 1, 27, 17, 0)
        assert run_summarization.time_overlaps(cal, start, end) is True


# ============================================================================
# build_calendar_aware_prompt() — with parsed example data
# ============================================================================

class TestBuildCalendarAwarePromptWithData:
    """Tests using real calendar data parsed from examples/calendar.org."""

    def test_with_parsed_example_calendar(self):
        """Should produce valid prompt using entries from example calendar."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        monday = [e for e in entries if e['date'] == '2026-01-26']
        calendar_text = run_summarization.format_calendar_for_prompt(monday, '2026-01-26')
        
        result = run_summarization.build_calendar_aware_prompt(
            base_prompt='Summarize this transcript.',
            calendar_text=calendar_text,
            meeting_date='2026-01-26',
            notes_context=''
        )
        
        assert 'CALENDAR CONTEXT FOR 2026-01-26' in result
        assert 'Edd / Sarah 1:1' in result
        assert 'Engineering Town Hall' in result
        assert 'Summarize this transcript.' in result

    def test_calendar_context_before_base_prompt(self):
        """Calendar context should be prepended to base prompt."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        monday = [e for e in entries if e['date'] == '2026-01-26']
        calendar_text = run_summarization.format_calendar_for_prompt(monday, '2026-01-26')
        
        result = run_summarization.build_calendar_aware_prompt(
            base_prompt='BASE PROMPT HERE',
            calendar_text=calendar_text,
            meeting_date='2026-01-26',
            notes_context=''
        )
        
        assert result.index('CALENDAR CONTEXT') < result.index('BASE PROMPT HERE')


# ============================================================================
# enrich_with_calendar() — mocked LLM
# ============================================================================

class TestEnrichWithCalendar:
    """Tests for enrich_with_calendar() with mocked LLM responses."""

    def _make_notes_file(self, tmpdir, title="Test Meeting", date="2026-01-26",
                         time="14:00", participants="Thabani, Edd", slug="test-meeting"):
        """Helper to create a notes org file."""
        notes_path = os.path.join(tmpdir, f'{date.replace("-", "")}-{slug}.org')
        content = (
            f"** {title} :note:transcribed:\n"
            f"[{date} Mon {time}]\n"
            ":PROPERTIES:\n"
            f":PARTICIPANTS: {participants}\n"
            f":SLUG: {slug}\n"
            ":END:\n\n"
            "TL;DR: Test meeting summary.\n"
        )
        with open(notes_path, 'w') as f:
            f.write(content)
        return notes_path

    def _make_transcript_file(self, tmpdir, slug="test-meeting", date="2026-01-26"):
        """Helper to create a transcript file."""
        transcript_path = os.path.join(tmpdir, f'{date.replace("-", "")}-{slug}.txt')
        with open(transcript_path, 'w') as f:
            f.write("Test transcript content.")
        return transcript_path

    def _mock_llm_response(self, matched=True, confidence=0.9, calendar_title="Thabani / Edd 1:1",
                           calendar_time="14:00-14:30", meeting_link=None,
                           suggested_title="Thabani / Edd 1:1: Test Discussion",
                           suggested_slug="thabani-edd-1-1"):
        """Create a mock subprocess.run result with LLM JSON response."""
        response = {
            "matched": matched,
            "confidence": confidence,
            "calendar_entry_number": 4,
            "calendar_title": calendar_title,
            "calendar_time": calendar_time,
            "meeting_link": meeting_link,
            "suggested_title": suggested_title,
            "suggested_slug": suggested_slug,
            "reasoning": "Participant match"
        }
        return mock.Mock(returncode=0, stdout=json.dumps(response), stderr='')

    def test_enriches_matching_entry(self):
        """Should add calendar properties when LLM finds a match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir)
            transcript_path = self._make_transcript_file(tmpdir)
            
            with mock.patch('subprocess.run', return_value=self._mock_llm_response()):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            # Should return (old_path, new_path) since slug changed
            assert result is not None
            old_path, new_path = result
            assert 'thabani-edd-1-1' in new_path
            
            # Read the new file and check enrichment
            with open(new_path) as f:
                content = f.read()
            assert ':CALENDAR_MATCH: Thabani / Edd 1:1' in content
            assert ':CALENDAR_TIME: 14:00-14:30' in content

    def test_skips_low_confidence_match(self):
        """Should skip enrichment when confidence is below threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir)
            transcript_path = self._make_transcript_file(tmpdir)
            
            low_confidence = self._mock_llm_response(matched=True, confidence=0.5)
            with mock.patch('subprocess.run', return_value=low_confidence):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            assert result is None

    def test_skips_no_match(self):
        """Should skip enrichment when LLM says no match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir)
            transcript_path = self._make_transcript_file(tmpdir)
            
            no_match = self._mock_llm_response(matched=False, confidence=0.0)
            with mock.patch('subprocess.run', return_value=no_match):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            assert result is None

    def test_handles_no_calendar_entries_for_date(self):
        """Should return None when no calendar entries exist for the meeting date."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create notes for a date not in the calendar
            notes_path = self._make_notes_file(tmpdir, date="2026-03-15")
            transcript_path = self._make_transcript_file(tmpdir, date="2026-03-15")
            
            result = run_summarization.enrich_with_calendar(
                notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            assert result is None

    def test_handles_llm_timeout(self):
        """Should return None gracefully on LLM timeout."""
        import subprocess
        
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir)
            transcript_path = self._make_transcript_file(tmpdir)
            
            with mock.patch('subprocess.run', side_effect=subprocess.TimeoutExpired('cmd', 60)):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            assert result is None

    def test_handles_malformed_llm_response(self):
        """Should return None when LLM returns non-JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir)
            transcript_path = self._make_transcript_file(tmpdir)
            
            bad_response = mock.Mock(returncode=0, stdout='Not valid JSON at all', stderr='')
            with mock.patch('subprocess.run', return_value=bad_response):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            assert result is None

    def test_renames_files_on_slug_change(self):
        """Should rename both org and transcript files when slug changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir)
            transcript_path = self._make_transcript_file(tmpdir)
            
            with mock.patch('subprocess.run', return_value=self._mock_llm_response()):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            assert result is not None
            old_path, new_path = result
            
            # Old file should be gone, new file should exist
            assert not os.path.exists(old_path)
            assert os.path.exists(new_path)
            
            # Transcript should also be renamed
            new_transcript = os.path.join(tmpdir, '20260126-thabani-edd-1-1.txt')
            assert os.path.exists(new_transcript)

    def test_no_rename_when_slug_unchanged(self):
        """Should not rename files when slug stays the same."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir, slug="thabani-edd-1-1")
            transcript_path = self._make_transcript_file(tmpdir, slug="thabani-edd-1-1")
            
            same_slug = self._mock_llm_response(suggested_slug="thabani-edd-1-1")
            with mock.patch('subprocess.run', return_value=same_slug):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            # Should return None — no rename happened (properties added in place)
            assert result is None

    def test_adds_meeting_link_property(self):
        """Should add MEETING_LINK property when LLM provides it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = self._make_notes_file(tmpdir)
            transcript_path = self._make_transcript_file(tmpdir)
            
            response_with_link = self._mock_llm_response(
                meeting_link="https://teams.microsoft.com/l/meetup-join/ghi789")
            with mock.patch('subprocess.run', return_value=response_with_link):
                result = run_summarization.enrich_with_calendar(
                    notes_path, transcript_path, EXAMPLE_CALENDAR)
            
            assert result is not None
            _, new_path = result
            with open(new_path) as f:
                content = f.read()
            assert ':MEETING_LINK: https://teams.microsoft.com/l/meetup-join/ghi789' in content


# ============================================================================
# End-to-end: parse → filter → format pipeline
# ============================================================================

class TestCalendarPipeline:
    """Integration tests for the parse → filter → format pipeline."""

    def test_filter_by_date_then_format(self):
        """Should filter entries by date and format for prompt."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        tuesday = [e for e in entries if e['date'] == '2026-01-27']
        
        result = run_summarization.format_calendar_for_prompt(tuesday, '2026-01-27')
        
        assert 'Company All Hands' in result
        assert 'Mia / Edd 1:1' in result
        assert 'Sprint Planning' in result
        # Monday entries should not appear
        assert 'Engineering Town Hall' not in result

    def test_multiple_one_on_ones_same_day(self):
        """Should handle multiple 1:1s on the same day."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        monday = [e for e in entries if e['date'] == '2026-01-26']
        one_on_ones = [e for e in monday
                       if len(e['participants']) == 2 and e['start_time']]
        
        # Should find Sarah 1:1 and Thabani 1:1
        assert len(one_on_ones) == 2
        titles = {e['title'] for e in one_on_ones}
        assert 'Edd / Sarah 1:1' in titles
        assert 'Thabani / Edd 1:1' in titles

    def test_date_with_no_entries(self):
        """Should produce 'no entries' message for dates without meetings."""
        entries = run_summarization.parse_calendar_org(EXAMPLE_CALENDAR)
        wednesday = [e for e in entries if e['date'] == '2026-01-28']
        
        result = run_summarization.format_calendar_for_prompt(wednesday, '2026-01-28')
        assert 'No calendar entries' in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
