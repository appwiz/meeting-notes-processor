#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Meeting Notes Processor

Processes transcripts from inbox directory, generates summaries with LLM,
and organizes files into transcripts/ and notes/ directories.

Supports --workspace argument (or WORKSPACE_DIR env var) for running against a separate data repository.
"""

import subprocess
import os
import argparse
import sys
import glob
import json
from datetime import datetime
from pathlib import Path
import shutil
import re

# Script directory for finding default prompt
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_workspace_paths(workspace_dir: str) -> dict:
    """Compute all workspace-relative paths."""
    return {
        'workspace': workspace_dir,
        'inbox': os.path.join(workspace_dir, 'inbox'),
        'transcripts': os.path.join(workspace_dir, 'transcripts'),
        'notes': os.path.join(workspace_dir, 'notes'),
    }


def get_default_prompt_file(workspace_dir: str) -> str:
    """Return the default prompt file path, preferring workspace over script directory."""
    workspace_prompt = os.path.join(workspace_dir, 'prompt.txt')
    if os.path.exists(workspace_prompt):
        return workspace_prompt
    return os.path.join(SCRIPT_DIR, 'prompt.txt')


def load_prompt_template(prompt_file: str | None, workspace_dir: str) -> str:
    """Load the prompt template from a file.
    
    If prompt_file is None, uses get_default_prompt_file() to find the default.
    """
    if prompt_file is None:
        prompt_file = get_default_prompt_file(workspace_dir)
    
    if not os.path.exists(prompt_file):
        print(f"Error: Prompt file not found: {prompt_file}")
        sys.exit(1)
    
    with open(prompt_file, 'r', encoding='utf-8') as f:
        return f.read()


def format_calendar_for_prompt(calendar_entries: list[dict], meeting_date: str) -> str:
    """Format calendar entries for inclusion in the summarization prompt."""
    if not calendar_entries:
        return "No calendar entries found for this date."
    
    lines = []
    for i, e in enumerate(calendar_entries, 1):
        time_str = f"{e['start_time']}-{e['end_time']}" if e['start_time'] else "all-day"
        participants = ', '.join(e['participants']) if e['participants'] else 'unknown'
        lines.append(f"{i}. [{time_str}] {e['title']}")
        lines.append(f"   Participants: {participants}")
        if e['meeting_links']:
            lines.append(f"   Meeting link: {e['meeting_links'][0]}")
        lines.append("")
    
    return '\n'.join(lines)


def build_calendar_aware_prompt(base_prompt: str, calendar_text: str, meeting_date: str) -> str:
    """Build a combined prompt that includes calendar context for single-pass processing."""
    
    calendar_instructions = f"""
## CALENDAR CONTEXT FOR {meeting_date}

You have access to the user's calendar for this date. Use this to:
1. IDENTIFY THE CORRECT PARTICIPANTS - transcription often mishears names
2. Match the meeting to a calendar entry if possible
3. Use the calendar to CORRECT speaker misidentification

{calendar_text}

## CRITICAL: Participant Identification Strategy

The transcript speaker labels are OFTEN WRONG due to transcription errors. Use this logic:

### Step 1: Cross-reference speakers with calendar
- Look at who speaks in the transcript
- Compare with calendar entries for this date/time
- Calendar participant names are AUTHORITATIVE - trust them over transcript labels

### Step 2: Common transcription errors to watch for
- "Kim" is often a mishearing of other names (Thabani, etc.)
- Names may be phonetically similar but wrong
- If transcript says "Kim" but calendar shows "Thabani 1:1" at that time, the speaker is Thabani

### Step 3: Handling 1:1 meetings
- Calendar format for 1:1s: "username / ewilderj 1:1" (e.g., "thabani11 / ewilderj 1:1")
- The username maps to a person (thabani11 = Thabani)
- If the transcript has 2 speakers and one is Edd, this is a 1:1

### Step 4: Slug naming based on CORRECTED participants
- For 1:1 meetings: ALWAYS use "firstname-edd-1-1" format (e.g., "marion-edd-1-1", "thabani-edd-1-1")
  - This is REQUIRED for any meeting with exactly 2 participants where one is Edd
  - Do NOT use topic-based slugs for 1:1s, even if the topic is interesting
- For small groups (3-4): include key names (e.g., "mia-brian-edd-tpm")  
- For large meetings (5+): use meeting type, NOT names (e.g., "engineering-town-hall", "cip-slt-sync")

### Step 5: Add calendar metadata to output
If you match to a calendar entry, add these properties to the :PROPERTIES: drawer:
- :CALENDAR_MATCH: <exact calendar title>
- :CALENDAR_TIME: <HH:MM-HH:MM from calendar>
- :MEETING_LINK: <video call URL if present>

## END CALENDAR CONTEXT

"""
    
    # Insert calendar instructions before the base prompt
    return calendar_instructions + base_prompt


def extract_slug_from_org(org_file_path):
    """Extract the slug from the org file's property drawer."""
    try:
        with open(org_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Look for :SLUG: property in the property drawer
        match = re.search(r':SLUG:\s+([a-z0-9-]+)', content, re.IGNORECASE)
        if match:
            slug = match.group(1).lower().strip()
            # Ensure it's valid and reasonable length
            if slug and len(slug) <= 50 and re.match(r'^[a-z0-9-]+$', slug):
                return slug
        
        # Fallback to 'meeting' if no valid slug found
        print("  Warning: No valid slug found in org file, using 'meeting'")
        return 'meeting'
    except Exception as e:
        print(f"  Error extracting slug: {e}")
        return 'meeting'

def get_date_from_file(filepath):
    """Extract date from filename if present (YYYYMMDD-), otherwise from mtime."""
    import re
    filename = os.path.basename(filepath)
    # Check for YYYYMMDD pattern at start of filename
    match = re.match(r'^(\d{8})-', filename)
    if match:
        return match.group(1)
    # Fall back to file modification time
    timestamp = os.path.getmtime(filepath)
    return datetime.fromtimestamp(timestamp).strftime('%Y%m%d')

def ensure_unique_filename(directory, base_name, extension):
    """Ensure filename is unique by appending counter if necessary."""
    filepath = os.path.join(directory, f"{base_name}.{extension}")
    if not os.path.exists(filepath):
        return filepath
    
    counter = 1
    while True:
        filepath = os.path.join(directory, f"{base_name}-{counter}.{extension}")
        if not os.path.exists(filepath):
            return filepath
        counter += 1


# ============================================================================
# Calendar Parsing Functions
# ============================================================================
# Calendar data is now passed to the LLM in the initial summarization prompt,
# allowing single-pass processing with correct participant identification.
# The build_calendar_prompt and enrich_with_calendar functions are retained
# for potential reprocessing of existing notes without transcripts.
# ============================================================================

def parse_calendar_org(calendar_path: str) -> list[dict]:
    """Parse calendar.org and extract meeting entries."""
    entries = []
    
    with open(calendar_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Pattern: * Title <timestamp>
    entry_pattern = re.compile(
        r'^\* (.+?) <(\d{4}-\d{2}-\d{2}) \w{3}(?: (\d{2}:\d{2})-(\d{2}:\d{2}))?>\s*\n(.*?)(?=^\* |\Z)',
        re.MULTILINE | re.DOTALL
    )
    
    for match in entry_pattern.finditer(content):
        title = match.group(1).strip()
        date_str = match.group(2)
        start_time = match.group(3)
        end_time = match.group(4)
        body = match.group(5).strip()
        
        # Extract PARTICIPANTS from properties
        participants = []
        participants_match = re.search(r':PARTICIPANTS:\s*(.+?)(?:\n|$)', body)
        if participants_match:
            raw_participants = participants_match.group(1)
            for p in raw_participants.split(','):
                p = p.strip()
                name = re.sub(r'\s*<[^>]+>\s*', '', p).strip()
                if name:
                    participants.append(name)
        
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
            'meeting_links': meeting_links,
        })
    
    return entries


def parse_notes_org_for_calendar(notes_path: str) -> dict:
    """Parse a notes.org file for calendar matching."""
    with open(notes_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    result = {
        'title': None, 'timestamp': None, 'date': None, 'time': None,
        'participants': [], 'slug': None, 'topic': None, 'content': content
    }
    
    # Extract title (first ** heading)
    title_match = re.search(r'^\*\* (.+?)\s+:note:', content, re.MULTILINE)
    if title_match:
        result['title'] = title_match.group(1).strip()
    
    # Extract timestamp [YYYY-MM-DD Day HH:MM]
    ts_match = re.search(r'\[(\d{4}-\d{2}-\d{2}) (\w{3})(?: (\d{2}:\d{2}))?\]', content)
    if ts_match:
        result['date'] = ts_match.group(1)
        result['time'] = ts_match.group(3)
        result['timestamp'] = ts_match.group(0)
    
    # Extract properties
    for prop in ['PARTICIPANTS', 'SLUG', 'TOPIC']:
        match = re.search(rf':{prop}:\s*(.+?)(?:\n|$)', content)
        if match:
            if prop == 'PARTICIPANTS':
                result['participants'] = [p.strip() for p in match.group(1).split(',')]
            else:
                result[prop.lower()] = match.group(1).strip()
    
    return result


def build_calendar_prompt(notes: dict, calendar_entries: list[dict]) -> str:
    """Build prompt for LLM calendar matching."""
    # Format calendar entries
    lines = []
    for i, e in enumerate(calendar_entries, 1):
        time_str = f"{e['start_time']}-{e['end_time']}" if e['start_time'] else "all-day"
        participants = ', '.join(e['participants']) if e['participants'] else 'unknown'
        lines.append(f"{i}. [{time_str}] {e['title']}")
        lines.append(f"   Participants: {participants}")
        if e['meeting_links']:
            lines.append(f"   Meeting link: {e['meeting_links'][0]}")
        lines.append("")
    calendar_text = '\n'.join(lines) if lines else "No calendar entries for this date."
    
    participants_str = ', '.join(notes['participants']) if notes['participants'] else 'unknown'
    participant_count = len(notes['participants']) if notes['participants'] else 0
    
    return f"""You are helping match a meeting transcript to a calendar entry.

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


def enrich_with_calendar(org_path: str, transcript_path: str, calendar_path: str, 
                          target: str = 'copilot', model: str = None, debug: bool = False) -> tuple[str, str] | None:
    """Enrich notes with calendar metadata. Returns (old_path, new_path) if renamed, else None."""
    
    # Parse calendar and notes
    calendar_entries = parse_calendar_org(calendar_path)
    notes = parse_notes_org_for_calendar(org_path)
    
    if not notes['date']:
        print("  Calendar: Could not extract date from notes")
        return None
    
    # Filter calendar to matching date
    day_entries = [e for e in calendar_entries if e['date'] == notes['date']]
    
    if not day_entries:
        print(f"  Calendar: No entries for {notes['date']}, skipping enrichment")
        return None
    
    print(f"  Calendar: Found {len(day_entries)} entries for {notes['date']}, matching...")
    
    # Build prompt and run LLM
    prompt = build_calendar_prompt(notes, day_entries)
    
    model_name = model if model else 'claude-sonnet-4.5'
    command = ['copilot', '-p', prompt, '--model', model_name]
    
    try:
        if debug:
            print(f"  Calendar: Running Copilot for matching...")
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            print(f"  Calendar: LLM error: {result.stderr[:200]}")
            return None
        
        # Extract JSON from output
        output = result.stdout.strip()
        json_match = re.search(r'\{[^{}]*\}', output, re.DOTALL)
        if not json_match:
            print("  Calendar: Could not parse LLM response")
            return None
        
        match_result = json.loads(json_match.group(0))
        
    except subprocess.TimeoutExpired:
        print("  Calendar: LLM timed out")
        return None
    except json.JSONDecodeError as e:
        print(f"  Calendar: JSON parse error: {e}")
        return None
    
    # Check if we have a confident match
    if not match_result.get('matched') or match_result.get('confidence', 0) < 0.7:
        print(f"  Calendar: No confident match (confidence: {match_result.get('confidence', 0):.0%})")
        return None
    
    print(f"  Calendar: Matched to '{match_result.get('calendar_title')}' ({match_result.get('confidence', 0):.0%})")
    
    # Apply enrichment
    content = notes['content']
    old_slug = notes['slug']
    new_slug = match_result.get('suggested_slug', old_slug)
    changes = []
    
    # Update title
    if match_result.get('suggested_title') and match_result['suggested_title'] != notes['title']:
        old_title_escaped = re.escape(notes['title'])
        content = re.sub(rf'(\*\* ){old_title_escaped}(\s+:note:)', 
                        f'\\1{match_result["suggested_title"]}\\2', content)
        changes.append(f"Title updated")
    
    # Update slug
    if new_slug and new_slug != old_slug:
        content = re.sub(r':SLUG:\s*.+?(?=\n)', f':SLUG: {new_slug}', content)
        changes.append(f"Slug: {old_slug} → {new_slug}")
    
    # Add calendar properties (before :END:)
    for prop, key in [('CALENDAR_MATCH', 'calendar_title'), 
                      ('CALENDAR_TIME', 'calendar_time'),
                      ('MEETING_LINK', 'meeting_link')]:
        if match_result.get(key) and f':{prop}:' not in content:
            content = re.sub(r'(:END:\s*\n)', f':{prop}: {match_result[key]}\n\\1', content)
            changes.append(f"Added {prop}")
    
    # Update timestamp
    if match_result.get('calendar_time') and notes['timestamp']:
        day_match = re.search(r'\d{4}-\d{2}-\d{2} (\w{3})', notes['timestamp'])
        if day_match:
            start_time = match_result['calendar_time'].split('-')[0]
            new_ts = f"[{notes['date']} {day_match.group(1)} {start_time}]"
            if notes['timestamp'] != new_ts:
                content = content.replace(notes['timestamp'], new_ts)
                changes.append("Timestamp updated")
    
    if changes:
        print(f"  Calendar: {', '.join(changes)}")
        with open(org_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # Rename files if slug changed
        if new_slug and new_slug != old_slug:
            date_str = notes['date'].replace('-', '')
            notes_dir = os.path.dirname(org_path)
            transcripts_dir = os.path.dirname(transcript_path)
            
            new_org_path = os.path.join(notes_dir, f"{date_str}-{new_slug}.org")
            new_transcript_path = os.path.join(transcripts_dir, f"{date_str}-{new_slug}.txt")
            
            if org_path != new_org_path:
                os.rename(org_path, new_org_path)
                print(f"  Renamed: {os.path.basename(org_path)} → {os.path.basename(new_org_path)}")
            if transcript_path != new_transcript_path and os.path.exists(transcript_path):
                os.rename(transcript_path, new_transcript_path)
                print(f"  Renamed: {os.path.basename(transcript_path)} → {os.path.basename(new_transcript_path)}")
            
            return (org_path, new_org_path)
    
    return None


# ============================================================================
# Main Processing Functions
# ============================================================================

def process_transcript(input_file, paths, target='copilot', model=None, prompt_template=None, debug=False, calendar_path=None):
    """Process a single transcript: summarize with calendar context, extract slug, and organize files."""
    print(f"\nProcessing: {input_file}")
    
    workspace_dir = paths['workspace']
    
    # Get date from file for naming and calendar lookup
    date_str = get_date_from_file(input_file)
    meeting_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"  # YYYY-MM-DD format
    temp_org_filename = f"temp-{date_str}.org"
    
    # Get basename for input file (relative to workspace)
    input_basename = os.path.basename(input_file)
    input_relative = os.path.join('inbox', input_basename)
    
    # Build the prompt - include calendar context if available
    final_prompt = prompt_template.format(input_file=input_relative, output_file=temp_org_filename)
    
    if calendar_path and os.path.exists(calendar_path):
        # Parse calendar and filter to matching date
        calendar_entries = parse_calendar_org(calendar_path)
        day_entries = [e for e in calendar_entries if e['date'] == meeting_date]
        
        if day_entries:
            print(f"  Calendar: Found {len(day_entries)} entries for {meeting_date}")
            calendar_text = format_calendar_for_prompt(day_entries, meeting_date)
            final_prompt = build_calendar_aware_prompt(final_prompt, calendar_text, meeting_date)
        else:
            print(f"  Calendar: No entries for {meeting_date}")
    
    # Run summarization
    print(f"  Generating summary...")

    if target == 'copilot':
        model_name = model if model else 'claude-sonnet-4.5'
        command = [
            'copilot',
            '-p', final_prompt,
            '--allow-tool', 'write',
            '--model', model_name
        ]
        try:
            if debug:
                print(f"  Running: {' '.join(command[:4])} '<prompt>' {' '.join(command[5:])}")
                print(f"  Working directory: {os.path.abspath(workspace_dir)}")
                print(f"  Prompt length: {len(final_prompt)} chars")
                print(f"  {'='*50}")
                print(f"  COPILOT OUTPUT:")
                print(f"  {'='*50}")
                # Stream output for debugging
                process = subprocess.Popen(
                    command,
                    cwd=workspace_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                for line in process.stdout:
                    print(f"  {line}", end='', flush=True)
                process.wait()
                print(f"  {'='*50}")
                print(f"  Exit code: {process.returncode}")
                if process.returncode != 0:
                    return False, None, None
            else:
                result = subprocess.run(command, capture_output=True, text=True, cwd=workspace_dir)
                if result.returncode != 0:
                    print(f"  Error in summarization: {result.stderr}")
                    return False, None, None
        except Exception as e:
            print(f"  Error running copilot: {e}")
            return False, None, None

    elif target == 'gemini':
        model_name = model if model else 'gemini-3-flash-preview'
        command = [
            'npx', '@google/gemini-cli',
            '--approval-mode', 'auto_edit',
            '--model', model_name
        ]
        try:
            if debug:
                print(f"  Running: {' '.join(command)}")
                print(f"  Working directory: {os.path.abspath(workspace_dir)}")
                print(f"  Prompt length: {len(final_prompt)} chars")
                print(f"  {'='*50}")
                print(f"  GEMINI OUTPUT:")
                print(f"  {'='*50}")
                # Stream output for debugging
                process = subprocess.Popen(
                    command,
                    cwd=workspace_dir,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                process.stdin.write(final_prompt)
                process.stdin.close()
                for line in process.stdout:
                    print(f"  {line}", end='', flush=True)
                process.wait()
                print(f"  {'='*50}")
                print(f"  Exit code: {process.returncode}")
                if process.returncode != 0:
                    return False, None, None
            else:
                result = subprocess.run(command, input=final_prompt, capture_output=True, text=True, cwd=workspace_dir)
                if result.returncode != 0:
                    print(f"  Error in summarization: {result.stderr}")
                    return False, None, None
        except Exception as e:
            print(f"  Error running gemini: {e}")
            return False, None, None
    
    # Check if org file was created (in workspace)
    temp_org_path = os.path.join(workspace_dir, temp_org_filename)
    if not os.path.exists(temp_org_path):
        print(f"  Error: Expected org file {temp_org_path} was not created")
        return False, None, None
    
    # Extract slug from the generated org file
    print("  Extracting slug from summary...")
    slug = extract_slug_from_org(temp_org_path)
    base_name = f"{date_str}-{slug}"
    print(f"  Using filename base: {base_name}")
    
    # Create final output paths (ensure uniqueness)
    transcript_path = ensure_unique_filename(paths['transcripts'], base_name, 'txt')
    org_path = ensure_unique_filename(paths['notes'], base_name, 'org')
    
    # Move files to their final locations
    shutil.move(temp_org_path, org_path)
    print(f"  Created: {org_path}")
    
    shutil.move(input_file, transcript_path)
    print(f"  Moved transcript to: {transcript_path}")
    
    return True, transcript_path, org_path

def git_commit_changes(inbox_files, transcript_files, org_files, workspace_dir):
    """Perform git operations: remove inbox files, add new files, and commit."""
    try:
        # Convert all file paths to be relative to workspace
        workspace_abs = os.path.abspath(workspace_dir)
        
        def make_relative(filepath):
            """Convert filepath to be relative to workspace."""
            abs_path = os.path.abspath(filepath)
            return os.path.relpath(abs_path, workspace_abs)
        
        # Stage deletions of inbox files (they've already been moved)
        # Use 'git add' to stage the deletions since files are already gone
        inbox_paths = [make_relative(f) for f in inbox_files]
        for rel_path in inbox_paths:
            result = subprocess.run(['git', 'add', rel_path], capture_output=True, text=True, cwd=workspace_dir)
            if result.returncode != 0:
                print(f"  Warning: git add (deletion) failed for {rel_path}: {result.stderr}")
            else:
                print(f"  Git staged deletion: {rel_path}")
        
        # Git add the new transcript and org files
        files_to_add = [make_relative(f) for f in transcript_files + org_files]
        if files_to_add:
            result = subprocess.run(['git', 'add'] + files_to_add, capture_output=True, text=True, cwd=workspace_dir)
            if result.returncode != 0:
                print(f"  Error: git add failed: {result.stderr}")
                return False
            else:
                for f in files_to_add:
                    print(f"  Git added: {f}")
        
        # Create commit message
        if len(transcript_files) == 1:
            # Single file - use its basename in message
            basename = os.path.basename(transcript_files[0])
            commit_msg = f"Process transcript: {basename}"
        else:
            # Multiple files
            commit_msg = f"Process {len(transcript_files)} transcripts"
        
        # Commit the changes
        result = subprocess.run(['git', 'commit', '-m', commit_msg], capture_output=True, text=True, cwd=workspace_dir)
        if result.returncode != 0:
            print(f"  Error: git commit failed: {result.stderr}")
            return False
        else:
            print(f"  Git committed: {commit_msg}")
            return True
            
    except Exception as e:
        print(f"  Error during git operations: {e}")
        return False

def process_inbox(paths, target='copilot', model=None, use_git=False, prompt_template=None, debug=False, calendar_path=None):
    """Process all transcript files in the inbox directory.
    
    Returns:
        tuple: (successful_count, failed_count) or (0, 0) if no files found
    """
    inbox_dir = paths['inbox']
    
    if not os.path.exists(inbox_dir):
        print(f"Error: {inbox_dir} directory not found.")
        return 0, 1  # Count as a failure
    
    # Find all .txt and .md files in inbox
    transcript_files = []
    for ext in ['*.txt', '*.md']:
        transcript_files.extend(glob.glob(os.path.join(inbox_dir, ext)))
    
    if not transcript_files:
        print(f"No transcript files found in {inbox_dir}/")
        return 0, 0  # No files is not a failure, but nothing succeeded
    
    print(f"Found {len(transcript_files)} transcript(s) to process")
    if calendar_path and os.path.exists(calendar_path):
        print(f"Calendar enrichment enabled: {calendar_path}")
    
    # Ensure output directories exist
    os.makedirs(paths['transcripts'], exist_ok=True)
    os.makedirs(paths['notes'], exist_ok=True)
    
    successful = 0
    failed = 0
    processed_inbox_files = []
    processed_transcript_files = []
    processed_org_files = []
    
    for transcript_file in transcript_files:
        try:
            result = process_transcript(transcript_file, paths, target, model, prompt_template, debug, calendar_path)
            if result[0]:  # Success
                successful += 1
                processed_inbox_files.append(transcript_file)
                processed_transcript_files.append(result[1])
                processed_org_files.append(result[2])
            else:
                failed += 1
        except Exception as e:
            print(f"Error processing {transcript_file}: {e}")
            failed += 1
    
    print(f"\n{'='*60}")
    print(f"Processing complete: {successful} successful, {failed} failed")
    print(f"{'='*60}")
    
    # Perform git operations if requested and there were successful processes
    if use_git and successful > 0:
        print(f"\nPerforming git operations...")
        if git_commit_changes(processed_inbox_files, processed_transcript_files, processed_org_files, paths['workspace']):
            print("Git operations completed successfully")
        else:
            print("Warning: Git operations failed")
    
    return successful, failed

def run_summarization():
    parser = argparse.ArgumentParser(
        description='Process meeting transcripts from inbox directory.',
        epilog='Processes all .txt and .md files in inbox/, generates summaries, and organizes files.'
    )
    parser.add_argument('--workspace', default=None,
                        help='Path to data repository. Default: WORKSPACE_DIR env var, or current directory.')
    parser.add_argument('--target', choices=['copilot', 'gemini'], default='copilot', 
                        help='The CLI tool to use (copilot or gemini). Default is copilot.')
    parser.add_argument('--model', help='The model to use. Defaults to claude-sonnet-4.5 for copilot and gemini-3-flash-preview for gemini.')
    parser.add_argument('--prompt', default=None,
                        help='Path to the prompt template file. Default: prompt.txt in workspace, or script directory as fallback.')
    parser.add_argument('--git', action='store_true',
                        help='Perform git operations: rm processed inbox files, add new files, and commit. For use in automation/CI.')
    parser.add_argument('--debug', action='store_true',
                        help='Stream AI output to terminal for debugging. Useful when processing hangs.')
    
    # Calendar enrichment options (Phase 7)
    calendar_group = parser.add_mutually_exclusive_group()
    calendar_group.add_argument('--calendar', action='store_true', default=True,
                               help='Enable calendar enrichment (default: enabled if calendar.org exists).')
    calendar_group.add_argument('--no-calendar', action='store_true',
                               help='Disable calendar enrichment.')
    
    args = parser.parse_args()
    
    # Determine workspace directory: CLI arg > env var > current directory
    workspace_dir = args.workspace or os.getenv('WORKSPACE_DIR', '.')
    paths = get_workspace_paths(workspace_dir)
    
    # Determine calendar path
    calendar_path = None
    if not args.no_calendar:
        potential_calendar = os.path.join(paths['workspace'], 'calendar.org')
        if os.path.exists(potential_calendar):
            calendar_path = potential_calendar
    
    # Load prompt template
    prompt_template = load_prompt_template(args.prompt, workspace_dir)
    
    # Ensure required directories exist
    for dir_path in [paths['inbox'], paths['transcripts'], paths['notes']]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            print(f"Created {dir_path}/ directory")
    
    # Process all transcripts in inbox
    result = process_inbox(paths, target=args.target, model=args.model, use_git=args.git, 
                          prompt_template=prompt_template, debug=args.debug, calendar_path=calendar_path)
    
    # Exit with appropriate code
    if result is None:
        sys.exit(1)  # Unexpected error
    successful, failed = result
    if failed > 0:
        sys.exit(1)  # Some files failed
    if successful == 0:
        sys.exit(2)  # No files were processed (not necessarily an error, but nothing happened)
    sys.exit(0)  # Success

if __name__ == "__main__":
    run_summarization()
