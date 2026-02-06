# Product Requirements Document: Meeting Notes Knowledge Base

## Overview

A system for automatically processing meeting transcripts into a searchable, organized knowledge base using org-mode format. The system ingests transcripts from various sources (MacWhisper, Zoom, Teams, etc.), generates meaningful summaries, and maintains a structured archive for long-term reference.

## Problem Statement

Meeting transcripts accumulate rapidly but lack organization and context, making them difficult to search and reference. Manual processing is time-consuming and inconsistent. This system automates the organization and summarization of meeting transcripts, creating a persistent knowledge base that preserves institutional memory.

## Goals

1. **Automation**: Automatically detect, process, and organize new meeting transcripts
2. **Discoverability**: Generate meaningful file names that aid in searching and browsing
3. **Summarization**: Extract key insights from transcripts using AI summarization
4. **Standardization**: Maintain consistent org-mode format for easy integration with knowledge management tools
5. **Preservation**: Create a durable archive of both original transcripts and summaries

## User Stories

- As a meeting participant, I want transcripts automatically processed so I don't have to manually organize them
- As a knowledge worker, I want meaningful file names so I can quickly locate relevant meetings
- As a team member, I want summarized meeting notes so I can review key points without reading full transcripts
- As a researcher, I want org-mode format so I can integrate meeting notes with my existing knowledge management system

## Functional Requirements

### 1. Transcript Ingestion

- **FR-1.1**: System processes all transcript files in the `inbox/` subdirectory when run
- **FR-1.2**: System accepts transcript formats: .txt and .md
- **FR-1.3**: System processes transcripts from MacWhisper, Zoom, Teams, and other sources

### 2. Content Analysis

- **FR-2.1**: System extracts key topics from transcript content to generate meaningful slugs
- **FR-2.2**: System determines meeting date from file timestamp
- **FR-2.3**: System generates AI-powered summaries using either Gemini or Copilot CLI (user selects at runtime)
- **FR-2.4**: `run_summarization.py` serves as the main program orchestrating slug generation and summarization

### 3. File Management

- **FR-3.1**: System moves processed transcripts from `inbox/` to `transcripts/`
- **FR-3.2**: System renames original transcripts to format: `YYYYMMDD-slug.txt`
- **FR-3.3**: System creates summary files in `notes/` directory with format: `YYYYMMDD-slug.org`
- **FR-3.4**: System ensures slug uniqueness to prevent filename collisions

### 4. Output Format

- **FR-4.1**: Summary files use org-mode format (.org extension)
- **FR-4.2**: Summary includes metadata header with date, source file, processing timestamp
- **FR-4.3**: Summary contains structured sections: participants, key topics, action items, decisions
- **FR-4.4**: Summary links back to original transcript file

### 5. Always-On Agent/Daemon

- **FR-5.1**: System can run as a long-lived daemon/service (always-on) with clear start/stop/restart behavior
- **FR-5.2**: Daemon syncs the configured data repository on startup and before processing new inbound work (e.g., `git pull --ff-only`)
- **FR-5.3**: Daemon supports two operating modes:
  - **Relay mode**: Commits incoming transcripts and triggers a GitHub Actions `workflow_dispatch` for cloud-based processing
  - **Standalone mode**: Processes transcripts locally by running the summarization script, then pushes results to remote
- **FR-5.4**: Daemon can optionally run a user-configured command hook when new data is detected after a sync (configurable)
- **FR-5.5**: Daemon logs sync/dispatch/hook outcomes clearly and fails safely (no partial writes or silent drops)
- **FR-5.6**: If the configured data-repo working directory does not contain a git checkout yet, daemon can bootstrap it by cloning the data repo before syncing/processing
- **FR-5.7**: Daemon provides `/calendar` endpoint to receive calendar.org updates via webhook (JSON or plain text)
- **FR-5.8**: Standalone mode supports async processing: return immediately after saving transcript, process in background thread

## Directory Structure

```
meeting-notes/
├── inbox/               # Drop zone for new transcripts
├── transcripts/         # Processed original transcripts
│   └── YYYYMMDD-slug.txt
├── notes/              # Generated org-mode summaries
│   └── YYYYMMDD-slug.org
├── run_summarization.py # Main program
├── package.json         # Node.js dependencies (Gemini CLI, Copilot)
└── PRD.md              # This document
```

## File Naming Convention

### Format: `YYYYMMDD-slug.{txt|org}`

- **YYYYMMDD**: Date derived from original file timestamp
- **slug**: 2-5 word descriptor generated from transcript content
  - Lowercase
  - Hyphen-separated
  - Derived from key topics/meeting subject
  - Examples: `quarterly-planning`, `product-roadmap-review`, `team-standup`

### Examples

- Original: `Meeting Recording 2025-12-29.txt` in inbox/
- Processed transcript: `transcripts/20251229-quarterly-planning.txt`
- Summary: `notes/20251229-quarterly-planning.org`

## Technical Requirements

### Dependencies

- **Python 3.x**: Core processing logic
- **@google/gemini-cli**: AI summarization option
- **@github/copilot**: Alternative AI summarization option
- **Node.js**: Required for CLI tools

### LLM Selection

The system supports two LLM backends that can be selected at runtime:
- **Gemini CLI** (`@google/gemini-cli`): Google's Gemini model
- **Copilot CLI** (`@github/copilot`): GitHub Copilot

User selects which LLM to use via command-line argument or configuration, providing flexibility based on availability, preference, or cost considerations.

### Processing Workflow

1. **Discovery**: Scan `inbox/` directory for transcript files (.txt, .md)
2. **Analysis**: 
   - Extract file creation/modification timestamp → YYYYMMDD
   - Analyze content with selected LLM to generate meaningful slug
   - Generate AI summary using selected LLM
3. **Organization**:
   - Move original transcript to `transcripts/YYYYMMDD-slug.txt`
   - Create summary file at `notes/YYYYMMDD-slug.org`
4. **Validation**: Ensure files were created successfully, handle errors
5. **Completion**: Report processing results

### Implementation Phases

#### Phase 1: Core Processing (Complete)
- ✅ Basic summarization in `run_summarization.py`
- ✅ Refactor `run_summarization.py` as main program
- ✅ Implement slug generation from transcript content
- ✅ Add LLM backend selection (Gemini or Copilot)
- ✅ Implement file renaming and organization
- ✅ Create org-mode formatted output
- ✅ Create separate `notes/` directory structure

#### Phase 2: Automation (Complete)
- ✅ Implement batch processing of inbox directory
- ✅ Add error handling and retry logic
- ✅ Create GitHub Actions workflow for automated processing
- ✅ Implement `--git` mode for automated git operations
- ✅ Configure workflow with proper permissions and tokens

#### Phase 3: Webhook Integration (Complete)
- ✅ Create local webhook daemon to receive MacWhisper transcripts
- ✅ Implement webhook endpoint with JSON payload parsing
- ✅ Add automated git commit and push from daemon
- ✅ Handle concurrent processing and git conflicts
- ✅ Add daemon logging and configuration file

#### Phase 4: Repository Separation (Complete)
- ✅ Separate code repository from data repository
- ✅ Configure webhook daemon to work with separate data repo
- ✅ Update GitHub Actions to work across repositories
- ✅ Document deployment and configuration for separated architecture
- ✅ Add remote repository configuration support
- ✅ Fix file path handling with `cwd` parameter in subprocess calls
- ✅ Fix git operations to use relative paths within data repository
- ✅ Unified token naming to `GH_TOKEN` for consistency
- ✅ Created example transcripts and test tooling

#### Phase 5: Always-On Agent/Daemon (Complete)
- ✅ Run continuously as a long-lived service (daemonization guidance)
- ✅ Keep the local data repo current via safe `git pull` semantics
- ✅ **Relay mode**: Trigger GitHub Actions via `workflow_dispatch` for cloud processing
- ✅ **Standalone mode**: Process transcripts locally and push results
- ✅ Optionally run a local command hook when new data arrives
- ✅ Renamed `webhook_daemon.py` to `meetingnotesd.py` to reflect broader responsibilities
- ✅ Auto-clone data repo if working directory doesn't exist

#### Phase 6: Enhancement (Future)
- ⏳ Add duplicate detection
- ⏳ Add search and indexing capabilities
- ⏳ Support additional LLM backends
- ⏳ Implement semantic search using vector embeddings
- ⏳ Add web interface for browsing processed notes

## Non-Functional Requirements

- **Performance**: Process transcript within 30 seconds per file
- **Reliability**: Handle errors gracefully without data loss
- **Scalability**: Support batch processing of 100+ transcripts
- **Maintainability**: Clear logging and error messages for troubleshooting
- **Security**: Handle sensitive meeting content securely, no external data leakage
- **Flexibility**: Easy switching between LLM backends based on user preference

## Success Metrics

- 100% of inbox transcripts automatically processed
- Average slug quality rated 4/5 or higher (meaningful and descriptive)
- Summary generation time < 30 seconds per transcript
- Zero data loss incidents
- Org-mode files successfully parse in Emacs/Org tools

## Future Considerations

- Web interface for browsing and searching processed meetings
- ~~Integration with calendar systems to auto-populate meeting metadata~~ (See Phase 7)
- Multi-language transcript support
- Advanced search using vector embeddings
- Automatic tagging and categorization
- Cross-referencing between related meetings
- Export to other formats (Markdown, HTML, PDF)

---

## Phase 7: Calendar Integration

### Status: MOSTLY COMPLETE

Core calendar matching and enrichment is implemented and working in production. Calendar context is included in the single-pass LLM summarization prompt, with time-based and participant-based matching. Recent notes context provides disambiguation for same-day meetings. The `/calendar` webhook endpoint on `meetingnotesd.py` receives calendar.org updates.

**Remaining:** Tests with example calendar data, README documentation.

### Overview

Enhance meeting notes by cross-referencing with a `calendar.org` file in the data repository. Calendar data is passed to the LLM **at summarization time** (single-pass), allowing real-time triangulation of transcript speakers against calendar attendees. This corrects transcription errors (e.g., "Kim" → "Alex") and ensures accurate participant identification.

### Problem Statement

Transcripts often lack precise metadata:
- Meeting titles from transcription software are generic ("Meeting Recording", "Zoom Call")
- Timestamps may be imprecise or only have dates without times
- Participant lists in transcripts may be incomplete or incorrect (speaker misidentification)
- Transcription software may confuse names (e.g., hearing "Kim" when the speaker said "Alex")

Calendar data provides authoritative information:
- Exact meeting titles as scheduled
- Precise start/end times
- Complete attendee lists
- Links to documents, video calls, and related resources

### Goals

1. **Accuracy**: Match transcripts to the correct calendar entries with high confidence
2. **Correction**: Use calendar attendees to correct transcription errors in speaker names
3. **Enrichment**: Add calendar metadata (title, time, attendees, links) to notes
4. **Non-destructive**: Preserve original AI-generated insights; augment, don't replace
5. **Optional**: Feature is opt-in; works without calendar file present

### Architecture: Single-Pass Processing

**Flow:**
```
transcript + calendar.org + recent_notes_context → LLM → notes.org (with accurate metadata)
```

The LLM receives:
1. **Transcript content** - who's speaking, what's discussed
2. **Calendar entries** - scheduled meetings for that day with attendees
3. **Notes context** - patterns from recent notes (who discusses what topics)

This allows the LLM to cross-reference in real-time:
- "This transcript has 'Kim' speaking about project topics at 10am"
- "Calendar shows Alex 1:1 at 10am, no meeting with Kim"
- "Prior notes show Alex discusses project topics"
- → "This is probably Alex, not Kim"

### Triangulation Strategy

The system uses multiple signals to match transcripts with calendar entries:

| Signal | Source | Weight | Notes |
|--------|--------|--------|-------|
| **Time** | Transcript timestamp / file mtime | High | Match within ±2 hours of calendar entry |
| **Participants** | `:PARTICIPANTS:` property in notes | High | Fuzzy match names (Edd → Edd Wilder-James) |
| **Subject** | Topic, slug, content keywords | Medium | Semantic similarity with calendar title/description |
| **Date** | Explicit date in transcript or file | Critical | Must be same day (hard constraint) |

**Matching Algorithm:**
1. Filter calendar entries by date (same day as transcript)
2. Score remaining entries by:
   - Time proximity (closer = higher score)
   - Participant overlap (more matches = higher score)
   - Subject similarity (semantic match via LLM)
3. If top score exceeds confidence threshold → match
4. If multiple entries tie or low confidence → no match (preserve original)

### Notes Context for Disambiguation

When multiple 1:1 meetings occur on the same day with similar topics, the system uses patterns from **prior notes** to disambiguate:

```
Recent meeting context:
- Alice Chen: "Platform architecture", "API releases", "career discussion"  
- Bob Smith: "Collaboration issues between Bob and Carol", "team dynamics"
```

If a transcript discusses "interpersonal challenges with Carol" but doesn't mention either Alice or Bob by name, the notes context helps the LLM recognize this matches Bob's typical discussion topics, not Alice's.

The `gather_recent_notes_context()` function extracts:
- Participant names from recent notes
- Meeting titles/topics associated with each participant
- This builds a "meeting memory" that improves accuracy over time

### Calendar.org Format

The system expects standard org-mode format with timestamps and properties:

```org
#+TITLE: Google Calendar Events

* Meeting Title <2026-01-20 Tue 09:05-09:45>
  :PROPERTIES:
  :PARTICIPANTS: Alice <alice@example.com>, Bob <bob@example.com>
  :LOCATION: https://zoom.us/j/123456
  :END:
  Meeting description and notes links...
```

**Required elements:**
- Heading with meeting title
- Org timestamp in angle brackets: `<YYYY-MM-DD Day HH:MM-HH:MM>`
- `:PARTICIPANTS:` property (optional but improves matching)

**Optional elements:**
- `:LOCATION:` property (video call links)
- Attached document/notes links
- Meeting description text

### Enrichment Output

When a calendar match is found, the notes file is updated:

```org
** 1:1 with Dana: Sales Pipeline Review :note:transcribed:
[2026-01-20 Tue 14:35-15:00]
:PROPERTIES:
:PARTICIPANTS: Dana Lee, Edd Wilder-James
:TOPIC: Quarterly sales pipeline and marketing alignment
:SLUG: dana-sales-pipeline
:CALENDAR_MATCH: Edd / Dana
:CALENDAR_TIME: 14:35-15:00
:MEETING_LINK: https://zoom.us/j/123456789
:END:
```

**Enrichment rules:**
1. **Title**: Incorporate calendar meeting title/participants for clarity
2. **Timestamp**: Update to exact calendar time if more precise
3. **Participants**: Merge calendar attendees with detected speakers
4. **New properties**: Add `CALENDAR_MATCH`, `CALENDAR_TIME`, `MEETING_LINK`
5. **Preserve**: Keep AI-generated TL;DR, actions, summary unchanged

### Implementation Options

#### Option A: Single LLM Pass (Recommended)

Use Copilot CLI to perform matching and enrichment in one prompt:

```bash
npx @github/copilot -p "
Given these calendar entries for [date]:
[calendar excerpt]

And this meeting note:
[notes.org content]

Determine if there's a matching calendar entry based on:
- Time overlap (note timestamp vs calendar times)
- Participant names (partial matches count)
- Subject/topic similarity

If confident match (>70%), output the enriched note with:
- Updated title (if calendar title is more specific)
- Exact time from calendar
- CALENDAR_MATCH property with matched entry title
- MEETING_LINK if available

If no confident match, output the original note unchanged.
"
```

**Pros:**
- Simple implementation
- LLM handles fuzzy matching naturally
- Single subprocess call

**Cons:**
- Uses LLM tokens for matching logic
- Less deterministic

#### Option B: Hybrid Approach

Python does date/time filtering, LLM does semantic matching:

1. **Python**: Parse `calendar.org`, filter to same-day entries
2. **Python**: Score entries by time proximity and participant overlap
3. **LLM**: For top candidates, ask for semantic subject match
4. **Python**: Apply enrichment based on final selection

**Pros:**
- More efficient token usage
- Deterministic date/time matching
- Better error handling

**Cons:**
- More complex implementation
- Requires org-mode parsing in Python

#### Option C: Full LLM Orchestration

Have the LLM read both files and make all decisions:

```bash
npx @github/copilot -p "
Read calendar.org and identify meetings on [date].
Read [notes.org] and determine which calendar entry matches.
Update [notes.org] with calendar metadata.
" --allow-tool read --allow-tool write
```

**Pros:**
- Simplest code
- LLM sees full context

**Cons:**
- Most expensive (token usage)
- May be slow for large calendars
- Less predictable

### Recommended Implementation: Option A with Pre-filtering

Combine efficiency with LLM intelligence:

1. **Python pre-processing**:
   - Read `calendar.org` if present (skip feature if missing)
   - Extract note's date from timestamp or file
   - Filter calendar to entries within ±1 day
   - Extract relevant calendar text (limit to ~10 entries)

2. **LLM matching prompt**:
   - Provide filtered calendar entries
   - Provide complete notes.org
   - Ask for match decision and enrichment
   - Request structured output (JSON or org diff)

3. **Python post-processing**:
   - Parse LLM response
   - Apply enrichment to notes file
   - Log match decision for debugging

### Configuration

Add to `config.yaml`:

```yaml
calendar:
  enabled: true
  file: calendar.org  # relative to data repo root
  confidence_threshold: 0.7  # minimum confidence for auto-match
  time_window_hours: 2  # how far from transcript time to search
  
  # Enrichment options
  enrich:
    update_title: true  # replace generic titles
    add_calendar_properties: true
    merge_participants: true
    add_meeting_links: true
```

Add CLI flag to `run_summarization.py`:

```bash
uv run run_summarization.py --calendar  # enable calendar matching
uv run run_summarization.py --no-calendar  # explicitly disable
```

### Workflow Integration

**After initial summarization:**
```python
def process_transcript(...):
    # ... existing summarization code ...
    
    # Phase 7: Calendar enrichment
    if calendar_enabled and os.path.exists(calendar_file):
        enrich_with_calendar(org_path, calendar_file, date_str)
```

**Calendar enrichment function:**
```python
def enrich_with_calendar(notes_path, calendar_path, date_str):
    """Enrich notes with calendar metadata if a match is found."""
    # 1. Load and filter calendar entries
    calendar_entries = parse_calendar_org(calendar_path)
    candidates = filter_by_date(calendar_entries, date_str)
    
    if not candidates:
        print(f"  No calendar entries for {date_str}, skipping enrichment")
        return
    
    # 2. Build LLM prompt with candidates and notes
    prompt = build_calendar_match_prompt(candidates, notes_path)
    
    # 3. Run LLM for matching and enrichment
    result = run_copilot_calendar_match(prompt, notes_path)
    
    if result.matched:
        print(f"  Matched to calendar: {result.calendar_title}")
    else:
        print(f"  No confident calendar match found")
```

### Functional Requirements

- **FR-7.1**: System reads `calendar.org` from data repo root if present
- **FR-7.2**: System filters calendar entries to same day as transcript
- **FR-7.3**: System uses LLM to match transcript with calendar entry based on time, participants, and subject
- **FR-7.4**: System adds calendar metadata properties to matched notes
- **FR-7.5**: System preserves original AI-generated content (summary, actions, questions)
- **FR-7.6**: System logs match decisions for debugging and audit
- **FR-7.7**: Feature is optional and gracefully disabled if calendar.org missing
- **FR-7.8**: System handles multiple transcripts from same day correctly

### Testing Strategy

1. **Unit tests**: Calendar parsing, date filtering, property merging
2. **Integration tests**: Full flow with test calendar and transcripts
3. **Edge cases**:
   - Multiple meetings same day with similar participants
   - Meeting time in transcript differs from calendar (rescheduled)
   - Participants in transcript not in calendar (drop-ins)
   - No calendar.org file present
   - Calendar entry has no PARTICIPANTS property

### Implementation Tasks

1. [x] Add calendar parsing utility (org-mode basic parser) — `parse_calendar_org()`
2. [x] Create calendar candidate filtering by date — `time_overlaps()` + date filtering
3. [x] Design and test LLM matching prompt — `build_calendar_aware_prompt()`, `build_calendar_prompt()`
4. [x] Implement enrichment function — `enrich_with_calendar()`
5. [x] Add `--calendar` / `--no-calendar` CLI flags
6. [x] Integrate into `process_transcript()` flow — calendar matching is single-pass with summarization
7. [x] Gather recent notes context for disambiguation — `gather_recent_notes_context()`
8. [x] Add tests with example calendar data
9. [x] Document calendar feature in README

### Open Questions

1. **Multi-match handling**: When multiple calendar entries seem equally valid, should we:
   - Ask user interactively?
   - Pick closest by time?
   - Add all as potential matches?
   
2. **Confidence exposure**: Should we add a `MATCH_CONFIDENCE` property so users know how certain the match was?

3. **Re-enrichment**: If notes are manually edited, should re-processing preserve calendar metadata?

4. **Calendar sources**: Future support for fetching live calendar data (Google Calendar API, Microsoft Graph)?

5. **Attendee matching**: How to handle name variations (nicknames, formal names, email-only)?

---

## Phase 3: MacWhisper Webhook Integration

### Overview

MacWhisper can send webhooks upon transcription completion. A local webhook daemon receives these webhooks and automatically commits transcripts to the inbox, triggering the existing GitHub Actions workflow for processing.

### Webhook Payload

MacWhisper sends simple JSON:
```json
{
  "title": "Meeting with John",
  "transcript": "Full transcript text..."
}
```

### Architecture: Webhook → Inbox → GitHub Actions

**Workflow:**
1. Local daemon receives webhook from MacWhisper
2. Daemon writes transcript to `inbox/{timestamp}-{sanitized-title}.txt`
3. Daemon commits and pushes to GitHub
4. GitHub Actions detects inbox commit and processes automatically (existing workflow)

**Advantages:**
- Separation of concerns: daemon only handles webhook → file → commit
- GitHub Actions handles all processing (centralized, logged)
- Processing happens in cloud (works when computer is off for subsequent transcripts)
- Git operations are isolated to GitHub Actions for LLM processing
- Daemon remains lightweight and reliable

### Technical Specification

#### Meeting Notes Daemon (`meetingnotesd.py`)

**Responsibilities:**
- Receive HTTP POST from MacWhisper
- Validate payload
- Sanitize title for filename
- Write transcript to inbox with timestamp
- Git add, commit, and push
- Return success/error to MacWhisper

**API Endpoint:**
```
POST http://localhost:8080/webhook
Content-Type: application/json

{
  "title": "string",
  "transcript": "string"
}
```

**Response:**
```json
{
  "status": "success",
  "filename": "20251230-142305-meeting-with-john.txt",
  "message": "Transcript queued for processing"
}
```

**Requirements:**
- Lightweight HTTP server (Flask/FastAPI)
- Minimal dependencies
- Timestamp-based filenames to prevent collisions
- Safe filename sanitization
- Git commit and push after writing file
- Logging for debugging
- Error handling and meaningful responses

**Filename Format:**
```
inbox/{YYYYMMDD-HHMMSS}-{sanitized-title}.txt
```

#### Configuration

```yaml
# config.yaml (optional)
daemon:
  host: 0.0.0.0
  port: 8080
  
git:
  auto_push: true
  commit_message_template: "Add transcript: {title}"
```

**Phase 5: Operating Modes**

The daemon (`meetingnotesd`) supports two operating modes, configured via `config.yaml`:

**Relay Mode** (cloud processing)
- Daemon receives webhook, writes transcript to inbox, commits and pushes
- Triggers GitHub Actions `workflow_dispatch` for cloud-based summarization
- Processing happens in GitHub Actions runner
- Best for: teams, audit trails, when cloud resources are preferred

**Standalone Mode** (local processing)
- Daemon receives webhook, writes transcript to inbox
- Runs `run_summarization.py` locally to process transcripts
- Commits all results (transcripts, notes) and pushes to remote
- Best for: single-user, privacy-sensitive, offline-capable setups

```yaml
# config.yaml - Relay Mode Example
data_repo: ../meeting-notes

git:
  auto_commit: true
  auto_push: true
  repository_url: "github.com/USER/meeting-notes.git"
  branch: main

sync:
  enabled: true
  on_startup: true

# Relay mode: trigger cloud workflow after pushing transcript
github:
  workflow_dispatch:
    enabled: true
    repo: "USER/meeting-notes"
    workflow: "process-transcripts.yml"
    ref: "main"

# Standalone mode disabled
processing:
  standalone:
    enabled: false
```

```yaml
# config.yaml - Standalone Mode Example
data_repo: ../meeting-notes

git:
  auto_commit: true
  auto_push: true
  repository_url: "github.com/USER/meeting-notes.git"
  branch: main

sync:
  enabled: true
  on_startup: true

# Relay mode disabled
github:
  workflow_dispatch:
    enabled: false

# Standalone mode: process locally after receiving webhook
processing:
  standalone:
    enabled: true
    command: "uv run run_summarization.py --git"
    working_directory: "."  # relative to processor repo, or absolute
    timeout_seconds: 600
```

**Additional config options (both modes):**

```yaml
# Keep the local data repo up to date
sync:
  enabled: true
  on_startup: true
  before_accepting_webhooks: true
  poll_interval_seconds: 60  # background sync (0 = disabled)
  ff_only: true

# Run a command when background sync pulls new commits
hooks:
  on_new_commits:
    enabled: false
    command: "echo 'New commits arrived'"
    working_directory: "."
    timeout_seconds: 600
```

### Implementation Tasks (Phase 3)

1. **Basic Webhook Daemon**
   - Create Flask/FastAPI HTTP server
   - Accept POST requests with JSON payloads
   - Validate required fields (title, transcript)
   - Return appropriate HTTP status codes

2. **File Handling**
   - Sanitize title for safe filenames
   - Add timestamp to prevent collisions
   - Write transcript to inbox directory
   - Handle file write errors gracefully

3. **Git Integration**
   - Auto-commit after receiving webhook
   - Auto-push to trigger GitHub Actions
   - Handle git conflicts and push failures
   - Retry logic for transient failures

4. **Reliability Features**
   - Request logging and audit trail
   - Health check endpoint (GET /)
   - Graceful error responses
   - Optional authentication via webhook secret

### Open Questions (Phase 3)

1. Should webhook endpoint require authentication/secret token?
2. How to handle git push failures (queue? retry? notify user)?
3. Should daemon validate transcript content length/format before accepting?
4. Should daemon support webhooks from other sources (Zoom, Teams)?
5. Daemon startup: systemd service, launchd, or manual start?



## Risks and Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| Slug generation produces non-unique names | Medium | Append counter suffix for duplicates |
| AI summarization API failures | High | Implement retry logic and fallback options |
| Large transcript files cause timeout | Medium | Implement chunking for large files |
| Incorrect date extraction | Low | Allow manual date override mechanism |
| Sensitive information in transcripts | High | Document security best practices, consider local LLM options |

## Open Questions

1. What should happen if a transcript lacks clear topics for slug generation?
2. Should there be a review/approval step before moving files, or fully automated?
3. How long should processed files remain in transcripts/ and notes/ before archiving?
4. Should the system support editing/reprocessing of already-processed transcripts?
5. What should be the default LLM if none is specified?
6. Should the system support processing subdirectories within inbox/?

---

## Phase 4: Repository Separation - Implementation Notes

### Status: COMPLETE ✅

The repository separation has been successfully implemented and tested. The system now supports both same-repository and separated-repository architectures, with separated being the recommended approach.

### What Was Built

**Two Repository Architecture:**
1. **Code Repository** (`meeting-notes-processor`) - This repository
   - Processing scripts with `WORKSPACE_DIR` environment variable support
   - Webhook daemon with configurable paths via `config.yaml`
   - GitHub Actions workflow template for data repositories
   - Example transcripts and testing utilities
   
2. **Data Repository** (user-created, e.g., `my-meeting-notes`)
   - `inbox/` - Drop zone for new transcripts
   - `transcripts/` - Processed original transcripts
   - `notes/` - AI-generated org-mode summaries
   - `.github/workflows/` - Optional automation

### Key Technical Achievements

**1. Path Handling with `cwd` Parameter**
- All subprocess calls (Copilot CLI, Gemini CLI, git commands) now use `cwd=WORKSPACE_DIR`
- This allows the processor to run from one directory while operating on files in another
- Solves the "outside repository" git errors that occurred with relative paths

**2. Relative Path Conversion for Git**
- Git operations convert all paths to be relative to `WORKSPACE_DIR` before execution
- Uses `os.path.relpath()` to compute paths from within the data repository
- Git properly detects file moves/renames (shows as R100 in commit history)

**3. Unified Token Management**
- Changed from `GITHUB_TOKEN` to `GH_TOKEN` throughout codebase
- Single token used for both webhook daemon (local) and GitHub Actions (cloud)
- Fine-grained Personal Access Token with:
  - Contents: Read and write
  - Copilot Requests (for Copilot CLI authentication)

**4. Example Transcripts**
Created three realistic example transcripts in `examples/`:
- `q1-planning-sarah.txt` - Business planning meeting
- `dunder-mifflin-sales.txt` - Sales strategy (The Office characters)
- `mad-men-heinz.txt` - Advertising brainstorm (Mad Men characters)

**5. Testing Utility**
- `send_transcript.py` - Sends transcripts to webhook daemon
- Uses PEP 723 inline script metadata for dependencies
- Simplifies testing and demonstration

### How It Works

#### Local Development with Separated Repos

```bash
# Directory structure
~/projects/
├── meeting-notes-processor/  (code repo)
└── my-meeting-notes/          (data repo)

# Configure processor
cd meeting-notes-processor
# Edit config.yaml to point to ../my-meeting-notes

# Process transcripts
WORKSPACE_DIR=../my-meeting-notes uv run run_summarization.py

# Run webhook daemon
GH_TOKEN=xxx uv run meetingnotesd.py
```

#### GitHub Actions

The data repository contains a workflow that:
1. Checks out both data repo and processor repo
2. Checks if inbox has files (skips if empty)
3. Runs processor with `WORKSPACE_DIR=../meeting-notes`
4. Processor commits results directly to data repo (using `--git` flag)

**Key improvement:** The processor handles git operations internally, eliminating the need for a separate commit step in the workflow.

### Configuration File Changes

**config.yaml in processor repo:**
```yaml
server:
  host: 127.0.0.1
  port: 9876

directories:
  inbox: ../my-meeting-notes/inbox
  repository: ../my-meeting-notes

git:
  auto_commit: true
  auto_push: true
  repository_url: "github.com/USERNAME/my-meeting-notes.git"
  commit_message_template: "Add transcript: {title}"
```

### Workflow Template

Created `workflows-templates/process-transcripts-data-repo.yml` with:
- Early inbox check to skip processing if empty
- Conditional execution of all steps
- Proper git configuration before processing
- `--git` flag for automated commits

### Documentation Updates

- **README.md**: Completely rewritten to prioritize separated repository setup
- **AGENTS.md**: Updated with `WORKSPACE_DIR` usage and `GH_TOKEN` examples
- **workflows-templates/README.md**: Instructions for using workflow templates

### Testing & Validation

Tested scenarios:
✅ Processing with `WORKSPACE_DIR` set (separated repos)
✅ Processing without `WORKSPACE_DIR` (same repo)
✅ Webhook daemon receiving and committing transcripts
✅ Git operations with proper file renames and deletions
✅ GitHub Actions workflow with empty and non-empty inbox
✅ Example transcripts through webhook test script

### Resolved Issues

**Git "outside repository" errors:**
- Fixed by using `cwd` parameter in subprocess calls
- Fixed by converting file paths to relative paths before git operations

**Inbox file deletion in git:**
- Changed from `git rm` to `git add` to stage deletions
- Git automatically detects moved files as renames

**Token confusion:**
- Unified to `GH_TOKEN` for both local and Actions use
- Documented fine-grained PAT requirements clearly

### Deployment Recommendation

**Option A (Implemented):** Data repo triggers processor via GitHub Actions
- Data repo's workflow checks out processor code
- Processor runs with `WORKSPACE_DIR` pointing to data repo
- Processor commits results back to data repo

This approach is preferred because:
- Natural trigger: new files in inbox
- Simple mental model
- All automation lives in data repo
- Processor repo is purely code (no workflows)

### Migration from Same-Repository

For users with existing same-repository setups:
1. Create new data repository with `inbox/`, `transcripts/`, `notes/`
2. Move data files from old repo to new data repo
3. Clone processor repo separately
4. Update `config.yaml` in processor repo
5. Test with `WORKSPACE_DIR` environment variable
6. Set up GitHub Actions workflow in data repo

The processor supports both models simultaneously - no code changes needed.

### Known Limitations

1. **Relative paths in config.yaml**: Must be relative to processor repo directory
2. **Manual token setup**: Users must create fine-grained PAT with correct permissions
3. **Two-repo cloning**: Initial setup requires cloning both repositories
4. **Path coordination**: Local development needs both repos in expected relative positions

### Future Enhancements (Phase 5)

Potential improvements:
- Config validation tool to check paths and permissions
- Setup script to bootstrap data repository structure
- Docker container with both repos configured
- Remote data repository support (not just local paths)
- Multiple data repositories per processor (team vs personal)

---
