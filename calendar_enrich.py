#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Calendar Enrichment Prototype

Cross-references meeting notes with calendar.org to enrich metadata.
Uses LLM (via Copilot CLI) for fuzzy matching of participants and subjects.
"""

import re
import os
import sys
import subprocess
import argparse
from datetime import datetime
from pathlib import Path


def parse_calendar_org(calendar_path: str) -> list[dict]:
    """Parse calendar.org and extract meeting entries.
    
    Returns list of dicts with: title, date, start_time, end_time, participants, location, body
    """
    entries = []
    
    with open(calendar_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split by top-level headings (single *)
    # Pattern: * Title <timestamp>
    entry_pattern = re.compile(
        r'^\* (.+?) <(\d{4}-\d{2}-\d{2}) \w{3}(?: (\d{2}:\d{2})-(\d{2}:\d{2}))?>\s*\n(.*?)(?=^\* |\Z)',
        re.MULTILINE | re.DOTALL
    )
    
    for match in entry_pattern.finditer(content):
        title = match.group(1).strip()
        date_str = match.group(2)
        start_time = match.group(3)  # May be None for all-day events
        end_time = match.group(4)
        body = match.group(5).strip()
        
        # Extract PARTICIPANTS from properties
        participants = []
        participants_match = re.search(r':PARTICIPANTS:\s*(.+?)(?:\n|$)', body)
        if participants_match:
            # Parse participants - may be comma-separated, may have emails
            raw_participants = participants_match.group(1)
            # Split by comma, extract names (strip emails)
            for p in raw_participants.split(','):
                p = p.strip()
                # Remove email in angle brackets
                name = re.sub(r'\s*<[^>]+>\s*', '', p).strip()
                if name:
                    participants.append(name)
        
        # Extract LOCATION
        location = None
        location_match = re.search(r':LOCATION:\s*(.+?)(?:\n|$)', body)
        if location_match:
            location = location_match.group(1).strip()
        
        # Extract video call links from body
        meeting_links = []
        link_pattern = re.compile(r'\[\[(https://[^\]]+)\]\[📹[^\]]*\]\]')
        for link_match in link_pattern.finditer(body):
            meeting_links.append(link_match.group(1))
        
        entries.append({
            'title': title,
            'date': date_str,
            'start_time': start_time,
            'end_time': end_time,
            'participants': participants,
            'location': location,
            'meeting_links': meeting_links,
            'body': body
        })
    
    return entries


def parse_notes_org(notes_path: str) -> dict:
    """Parse a notes.org file and extract key metadata."""
    with open(notes_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    result = {
        'title': None,
        'timestamp': None,
        'date': None,
        'time': None,
        'participants': [],
        'slug': None,
        'topic': None,
        'content': content
    }
    
    # Extract title (first ** heading) - everything before the tags like :note:transcribed:
    title_match = re.search(r'^\*\* (.+?)\s+:note:', content, re.MULTILINE)
    if title_match:
        result['title'] = title_match.group(1).strip()
    
    # Extract timestamp [YYYY-MM-DD] or [YYYY-MM-DD Day] or [YYYY-MM-DD Day HH:MM]
    ts_match = re.search(r'\[(\d{4}-\d{2}-\d{2})(?:\s+\w{3})?(?:\s+(\d{2}:\d{2}))?\]', content)
    if ts_match:
        result['date'] = ts_match.group(1)
        result['time'] = ts_match.group(2)  # Will be None if no time present
        result['timestamp'] = ts_match.group(0)
    
    # Extract PARTICIPANTS
    participants_match = re.search(r':PARTICIPANTS:\s*(.+?)(?:\n|$)', content)
    if participants_match:
        result['participants'] = [p.strip() for p in participants_match.group(1).split(',')]
    
    # Extract SLUG
    slug_match = re.search(r':SLUG:\s*(.+?)(?:\n|$)', content)
    if slug_match:
        result['slug'] = slug_match.group(1).strip()
    
    # Extract TOPIC
    topic_match = re.search(r':TOPIC:\s*(.+?)(?:\n|$)', content)
    if topic_match:
        result['topic'] = topic_match.group(1).strip()
    
    return result


def filter_calendar_by_date(entries: list[dict], target_date: str) -> list[dict]:
    """Filter calendar entries to those matching the target date."""
    return [e for e in entries if e['date'] == target_date]


def format_calendar_for_prompt(entries: list[dict]) -> str:
    """Format calendar entries for inclusion in LLM prompt."""
    if not entries:
        return "No calendar entries for this date."
    
    lines = []
    for i, e in enumerate(entries, 1):
        time_str = f"{e['start_time']}-{e['end_time']}" if e['start_time'] else "all-day"
        participants = ', '.join(e['participants']) if e['participants'] else 'unknown'
        lines.append(f"{i}. [{time_str}] {e['title']}")
        lines.append(f"   Participants: {participants}")
        if e['meeting_links']:
            lines.append(f"   Meeting link: {e['meeting_links'][0]}")
        lines.append("")
    
    return '\n'.join(lines)


def build_enrichment_prompt(notes: dict, calendar_entries: list[dict]) -> str:
    """Build the prompt for LLM to match and enrich."""
    
    calendar_text = format_calendar_for_prompt(calendar_entries)
    participants_str = ', '.join(notes['participants']) if notes['participants'] else 'unknown'
    participant_count = len(notes['participants']) if notes['participants'] else 0
    
    prompt = f"""You are helping match a meeting transcript to a calendar entry.

## Calendar entries for {notes['date']}:

{calendar_text}

## Meeting notes metadata:
- Title: {notes['title']}
- Time in notes: {notes['time'] or 'unknown'}
- PARTICIPANTS DETECTED: {participants_str}
- Participant count: {participant_count}
- Topic: {notes['topic'] or 'unknown'}
- Current slug: {notes['slug']}

## CRITICAL: Participant-First Matching Strategy

The PARTICIPANTS field is your PRIMARY matching signal. Follow this decision tree:

### Step 1: Check for participant name matches
Look at each calendar entry's participants and check if the detected participants match:
- Handle name variations: "Mia" = "its-mia" = "Mia Arts", "Thabani" = "thabani11", etc.
- Calendar titles like "cmart12 / ewilderj" mean participants are Chris Martin and Edd
- Calendar format is usually "username / username" for 1:1s

### Step 2: Decide based on participant matching

**IF participants clearly match a calendar entry:**
- This is your match! High confidence (85-95%)
- The topic/content of the meeting is IRRELEVANT - people discuss many topics in 1:1s
- Do NOT change the match based on what was discussed

**IF participants DON'T match any calendar entry but we have a time-proximate entry:**
- The transcript MAY have misidentified participants
- Common transcription errors: hearing "Kim" when speaker said "CIP", mishearing names
- In this case, use the calendar entry to CORRECT the participant identification
- Lower confidence (70-80%) since we're correcting a potential error

**IF {participant_count} participants detected (one being Edd) = 2:**
- This is almost certainly a 1:1 meeting
- STRONGLY PREFER matching to a 1:1 calendar entry (format: "name / ewilderj 1:1")
- Do NOT match to team syncs, SLT meetings, or group meetings just because topics overlap

### Step 3: Time as tiebreaker
- Use time proximity ONLY to choose between multiple entries with similar participant matches
- Meetings often run over, so notes time of 13:08 could match 12:30-13:00 or 13:00-13:30 slots
- Time is NOT a reason to override a clear participant match

### What NOT to do:
- DON'T match based on topic similarity alone - Edd discusses similar TPM topics in many meetings
- DON'T match to "CIP SLT Sync" just because CIP was discussed - check WHO was in the meeting
- DON'T let meeting title keywords override participant matching

## Response format (output ONLY this JSON, no other text):
{{
  "matched": true/false,
  "confidence": 0.0-1.0,
  "calendar_entry_number": N or null,
  "calendar_title": "exact title from calendar" or null,
  "calendar_time": "HH:MM-HH:MM" or null,
  "meeting_link": "URL" or null,
  "suggested_title": "Improved title incorporating calendar info" or null,
  "suggested_slug": "improved-slug-with-participant-names" or null,
  "reasoning": "Brief explanation: which participants matched which calendar entry"
}}

## Slug guidelines:
- For 1:1 meetings: "firstname-edd-1-1" (e.g., "mia-edd-1-1", "thabani-edd-1-1")
- For small groups (3-4): include key names (e.g., "mia-brian-edd-tpm")
- For large meetings (5+): use meeting type, NOT names (e.g., "engineering-town-hall")

## Title guidelines:
- For 1:1s: "Name / Edd 1:1: Topic Summary" - ALWAYS preserve the original topic
- For groups: keep the descriptive title from the notes

Output ONLY the JSON object, nothing else."""

    return prompt


def run_copilot_match(prompt: str, debug: bool = False) -> dict | None:
    """Run Copilot CLI to perform the matching."""
    import json
    
    command = [
        'copilot',
        '-p', prompt,
        '--model', 'claude-sonnet-4.5'
    ]
    
    if debug:
        print("Running Copilot for calendar matching...")
        print(f"Prompt length: {len(prompt)} chars")
    
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            print(f"Error from Copilot: {result.stderr}")
            return None
        
        output = result.stdout.strip()
        
        if debug:
            print(f"Raw output:\n{output}")
        
        # Try to extract JSON from output (may have extra text)
        json_match = re.search(r'\{[^{}]*\}', output, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        else:
            print("Could not find JSON in Copilot output")
            return None
            
    except subprocess.TimeoutExpired:
        print("Copilot timed out")
        return None
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
        return None


def apply_enrichment(notes_path: str, notes: dict, match_result: dict, 
                     calendar_entries: list[dict], dry_run: bool = True) -> tuple[str, str] | None:
    """Apply enrichment to the notes file.
    
    Returns (old_slug, new_slug) if slug changed, None otherwise.
    """
    if not match_result.get('matched'):
        return None
    
    content = notes['content']
    old_slug = notes['slug']
    new_slug = match_result.get('suggested_slug', old_slug)
    
    # Build new/updated properties
    entry_num = match_result.get('calendar_entry_number')
    calendar_entry = calendar_entries[entry_num - 1] if entry_num else None
    
    changes = []
    
    # Update title if suggested (do this first, before property changes)
    if match_result.get('suggested_title') and match_result['suggested_title'] != notes['title']:
        # Match the full heading line: ** title :tags:
        old_title_escaped = re.escape(notes['title'])
        # Replace the title, keeping the tags
        content = re.sub(
            rf'(\*\* ){old_title_escaped}(\s+:note:)',
            f'\\1{match_result["suggested_title"]}\\2',
            content
        )
        changes.append(f"Title: {notes['title']} → {match_result['suggested_title']}")
    
    # Update SLUG if changed
    if new_slug and new_slug != old_slug:
        content = re.sub(
            r':SLUG:\s*.+?(?=\n)',
            f':SLUG: {new_slug}',
            content
        )
        changes.append(f"SLUG: {old_slug} → {new_slug}")
    
    # Add CALENDAR_MATCH property (before :END:)
    if match_result.get('calendar_title'):
        if ':CALENDAR_MATCH:' not in content:
            content = re.sub(
                r'(:END:\s*\n)',
                f':CALENDAR_MATCH: {match_result["calendar_title"]}\n\\1',
                content
            )
            changes.append(f"Added CALENDAR_MATCH: {match_result['calendar_title']}")
    
    # Add CALENDAR_TIME property
    if match_result.get('calendar_time'):
        if ':CALENDAR_TIME:' not in content:
            content = re.sub(
                r'(:END:\s*\n)',
                f':CALENDAR_TIME: {match_result["calendar_time"]}\n\\1',
                content
            )
            changes.append(f"Added CALENDAR_TIME: {match_result['calendar_time']}")
    
    # Add MEETING_LINK property
    if match_result.get('meeting_link'):
        if ':MEETING_LINK:' not in content:
            content = re.sub(
                r'(:END:\s*\n)',
                f':MEETING_LINK: {match_result["meeting_link"]}\n\\1',
                content
            )
            changes.append(f"Added MEETING_LINK")
    
    # Update timestamp to be more precise if we have calendar time
    # Keep original day-of-week from the file
    if match_result.get('calendar_time') and notes['date'] and notes['timestamp']:
        # Extract day abbreviation from original timestamp
        day_match = re.search(r'\d{4}-\d{2}-\d{2} (\w{3})', notes['timestamp'])
        if day_match:
            day_abbr = day_match.group(1)
            start_time = match_result['calendar_time'].split('-')[0]
            new_timestamp = f"[{notes['date']} {day_abbr} {start_time}]"
            if notes['timestamp'] != new_timestamp:
                content = content.replace(notes['timestamp'], new_timestamp)
                changes.append(f"Timestamp: {notes['timestamp']} → {new_timestamp}")
    
    if changes:
        print(f"\nEnrichment changes:")
        for c in changes:
            print(f"  • {c}")
        
        if dry_run:
            print(f"\n[DRY RUN] Would write to: {notes_path}")
            print("---")
            # Show first 40 lines of enriched content
            for i, line in enumerate(content.split('\n')[:40]):
                print(line)
            print("---")
        else:
            with open(notes_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"\nWrote enriched content to: {notes_path}")
        
        if new_slug != old_slug:
            return (old_slug, new_slug)
    
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Enrich meeting notes with calendar metadata'
    )
    parser.add_argument('notes_file', help='Path to the notes.org file to enrich')
    parser.add_argument('--calendar', default=None,
                        help='Path to calendar.org. Default: looks in workspace root')
    parser.add_argument('--workspace', default=None,
                        help='Path to data workspace (for finding calendar.org)')
    parser.add_argument('--dry-run', action='store_true', default=True,
                        help='Show changes without writing (default)')
    parser.add_argument('--apply', action='store_true',
                        help='Actually apply changes')
    parser.add_argument('--debug', action='store_true',
                        help='Show debug output')
    
    args = parser.parse_args()
    
    # Find calendar.org
    if args.calendar:
        calendar_path = args.calendar
    elif args.workspace:
        calendar_path = os.path.join(args.workspace, 'calendar.org')
    else:
        # Try common locations
        for path in ['calendar.org', '../meeting-notes/calendar.org']:
            if os.path.exists(path):
                calendar_path = path
                break
        else:
            print("Error: Could not find calendar.org. Use --calendar or --workspace")
            sys.exit(1)
    
    if not os.path.exists(calendar_path):
        print(f"Error: Calendar file not found: {calendar_path}")
        sys.exit(1)
    
    if not os.path.exists(args.notes_file):
        print(f"Error: Notes file not found: {args.notes_file}")
        sys.exit(1)
    
    print(f"Calendar: {calendar_path}")
    print(f"Notes: {args.notes_file}")
    
    # Parse files
    calendar_entries = parse_calendar_org(calendar_path)
    print(f"Loaded {len(calendar_entries)} calendar entries")
    
    notes = parse_notes_org(args.notes_file)
    print(f"\nNotes metadata:")
    print(f"  Title: {notes['title']}")
    print(f"  Date: {notes['date']}")
    print(f"  Time: {notes['time']}")
    print(f"  Participants: {notes['participants']}")
    print(f"  Slug: {notes['slug']}")
    
    if not notes['date']:
        print("\nError: Could not extract date from notes file")
        sys.exit(1)
    
    # Filter calendar to matching date
    day_entries = filter_calendar_by_date(calendar_entries, notes['date'])
    print(f"\nCalendar entries for {notes['date']}: {len(day_entries)}")
    
    if not day_entries:
        print("No calendar entries for this date - skipping enrichment")
        sys.exit(0)
    
    if args.debug:
        print("\nFiltered calendar entries:")
        print(format_calendar_for_prompt(day_entries))
    
    # Build prompt and run LLM
    prompt = build_enrichment_prompt(notes, day_entries)
    
    if args.debug:
        print(f"\n{'='*60}")
        print("PROMPT:")
        print(prompt)
        print(f"{'='*60}")
    
    match_result = run_copilot_match(prompt, debug=args.debug)
    
    if not match_result:
        print("\nFailed to get match result from LLM")
        sys.exit(1)
    
    print(f"\nMatch result:")
    print(f"  Matched: {match_result.get('matched')}")
    print(f"  Confidence: {match_result.get('confidence')}")
    print(f"  Calendar entry: {match_result.get('calendar_title')}")
    print(f"  Reasoning: {match_result.get('reasoning')}")
    
    if match_result.get('matched') and match_result.get('confidence', 0) >= 0.7:
        print(f"\n✓ Confident match found!")
        slug_change = apply_enrichment(
            args.notes_file, 
            notes, 
            match_result, 
            day_entries,
            dry_run=not args.apply
        )
        
        if slug_change:
            old_slug, new_slug = slug_change
            date_prefix = notes['date'].replace('-', '')
            notes_dir = os.path.dirname(args.notes_file)
            workspace_dir = os.path.dirname(notes_dir)
            
            old_notes = args.notes_file
            new_notes = os.path.join(notes_dir, f"{date_prefix}-{new_slug}.org")
            
            transcripts_dir = os.path.join(workspace_dir, 'transcripts')
            old_transcript = os.path.join(transcripts_dir, f"{date_prefix}-{old_slug}.txt")
            new_transcript = os.path.join(transcripts_dir, f"{date_prefix}-{new_slug}.txt")
            
            if args.apply:
                # Rename notes file
                if old_notes != new_notes:
                    os.rename(old_notes, new_notes)
                    print(f"\nRenamed: {os.path.basename(old_notes)} → {os.path.basename(new_notes)}")
                
                # Rename transcript if it exists
                if os.path.exists(old_transcript):
                    os.rename(old_transcript, new_transcript)
                    print(f"Renamed: {os.path.basename(old_transcript)} → {os.path.basename(new_transcript)}")
            else:
                print(f"\n[DRY RUN] Would also rename files:")
                print(f"  {os.path.basename(old_notes)} → {os.path.basename(new_notes)}")
                if os.path.exists(old_transcript):
                    print(f"  {os.path.basename(old_transcript)} → {os.path.basename(new_transcript)}")
    else:
        print(f"\n✗ No confident match - notes unchanged")


if __name__ == "__main__":
    main()
