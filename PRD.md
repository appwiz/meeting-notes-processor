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

#### Phase 4: Repository Separation (Proposed)
- ⏳ Separate code repository from data repository
- ⏳ Configure webhook daemon to work with separate data repo
- ⏳ Update GitHub Actions to work across repositories
- ⏳ Document deployment and configuration for separated architecture
- ⏳ Add remote repository configuration support

#### Phase 5: Enhancement
- ⏳ Add configuration file for default LLM and other settings
- ⏳ Implement duplicate detection
- ⏳ Add search and indexing capabilities
- ⏳ Support additional LLM backends

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
- Integration with calendar systems to auto-populate meeting metadata
- Multi-language transcript support
- Advanced search using vector embeddings
- Automatic tagging and categorization
- Cross-referencing between related meetings
- Export to other formats (Markdown, HTML, PDF)

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

#### Webhook Daemon (`webhook_daemon.py`)

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

## Phase 4: Repository Separation

### Problem Statement

Currently, the processing code (`run_summarization.py`, `webhook_daemon.py`, etc.) lives in the same repository as the data (transcripts and notes). This creates several issues:

1. **Mixing concerns**: Code changes trigger GitHub Actions even when no transcripts need processing
2. **Version history pollution**: Data changes make it harder to track code evolution
3. **Access control**: Different teams may need different permissions for code vs. data
4. **Deployment complexity**: Code updates require touching the data repository
5. **Scaling**: Large data repositories make code cloning slow

### Proposed Architecture

**Two Repositories:**

1. **Code Repository** (`meeting-notes-processor`)
   - `run_summarization.py`
   - `webhook_daemon.py`
   - `config.yaml`
   - `.github/workflows/`
   - `package.json`
   - Documentation (README, PRD, AGENTS)
   
2. **Data Repository** (`meeting-notes`)
   - `inbox/`
   - `transcripts/`
   - `notes/`
   - Optional: README describing archive structure

### How It Works

#### Webhook Daemon Scenario

```yaml
# config.yaml in meeting-notes-processor
directories:
  inbox: ../meeting-notes/inbox          # Relative path to data repo
  repository: ../meeting-notes           # Data repo root
  
git:
  repository_url: "github.com/ewilderj/meeting-notes.git"
```

**Workflow:**
1. MacWhisper sends webhook to daemon (running from code repo)
2. Daemon writes transcript to `../meeting-notes/inbox/`
3. Daemon commits and pushes to data repo (`meeting-notes`)
4. GitHub Actions in data repo triggers and processes transcript
5. Processing results committed back to data repo

#### GitHub Actions Scenario

**Option A: Data Repo Triggers Code Repo**

```yaml
# .github/workflows/process-transcripts.yml (in meeting-notes data repo)
on:
  push:
    paths:
      - 'inbox/**'

jobs:
  process:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout data repo
        uses: actions/checkout@v4
        with:
          path: meeting-notes
          
      - name: Checkout processor repo
        uses: actions/checkout@v4
        with:
          repository: ewilderj/meeting-notes-processor
          path: processor
          
      - name: Setup dependencies
        run: |
          cd processor
          npm install
          
      - name: Process transcripts
        run: |
          cd processor
          uv run run_summarization.py --git
        env:
          WORKSPACE_DIR: ../meeting-notes
          GH_TOKEN: ${{ secrets.GH_TOKEN }}
          
      - name: Commit results
        run: |
          cd meeting-notes
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add transcripts/ notes/
          git commit -m "Process transcripts" || echo "No changes"
          git push
```

**Option B: Code Repo Monitors Data Repo**

```yaml
# .github/workflows/watch-data-repo.yml (in meeting-notes-processor code repo)
on:
  schedule:
    - cron: '*/5 * * * *'  # Check every 5 minutes
  workflow_dispatch:

jobs:
  check-and-process:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout processor
        uses: actions/checkout@v4
        
      - name: Checkout data repo
        uses: actions/checkout@v4
        with:
          repository: ewilderj/meeting-notes
          path: data
          token: ${{ secrets.GH_TOKEN }}
          
      - name: Check for inbox files
        id: check
        run: |
          if [ -n "$(ls -A data/inbox/*.txt 2>/dev/null)" ]; then
            echo "has_files=true" >> $GITHUB_OUTPUT
          fi
          
      - name: Process if needed
        if: steps.check.outputs.has_files == 'true'
        run: |
          uv run run_summarization.py --git
        env:
          WORKSPACE_DIR: ./data
          GH_TOKEN: ${{ secrets.GH_TOKEN }}
```

### Configuration Changes Needed

#### `run_summarization.py`

Add support for `WORKSPACE_DIR` environment variable:

```python
WORKSPACE_DIR = os.getenv('WORKSPACE_DIR', '.')
INBOX_DIR = os.path.join(WORKSPACE_DIR, 'inbox')
TRANSCRIPTS_DIR = os.path.join(WORKSPACE_DIR, 'transcripts')
NOTES_DIR = os.path.join(WORKSPACE_DIR, 'notes')
```

#### `webhook_daemon.py`

Already supports configurable directories via `config.yaml`:

```yaml
directories:
  inbox: /absolute/path/to/meeting-notes/inbox
  repository: /absolute/path/to/meeting-notes
```

Or relative paths:

```yaml
directories:
  inbox: ../meeting-notes/inbox
  repository: ../meeting-notes
```

### Deployment Models

#### Local Development

```
~/projects/
├── meeting-notes-processor/  (code repo)
│   ├── run_summarization.py
│   ├── webhook_daemon.py
│   └── config.yaml (points to ../meeting-notes)
└── meeting-notes/            (data repo)
    ├── inbox/
    ├── transcripts/
    └── notes/
```

Running daemon:
```bash
cd meeting-notes-processor
uv run webhook_daemon.py
```

#### Cloud/Server Deployment

```bash
# Clone both repos
git clone https://github.com/ewilderj/meeting-notes-processor.git
git clone https://github.com/ewilderj/meeting-notes.git

# Configure processor
cd meeting-notes-processor
cat > config.yaml <<EOF
server:
  host: 0.0.0.0
  port: 9876

directories:
  inbox: ../meeting-notes/inbox
  repository: ../meeting-notes

git:
  auto_commit: true
  auto_push: true
  repository_url: "github.com/ewilderj/meeting-notes.git"
EOF

# Run daemon (with token for git push)
GITHUB_TOKEN=xxx uv run webhook_daemon.py &
```

### Migration Path

1. **Create new code repository** (`meeting-notes-processor`)
   - Move Python scripts, config, workflows
   - Update paths in config and workflows
   - Test locally with both repos

2. **Update data repository** (`meeting-notes`)
   - Remove code files
   - Keep only data directories
   - Update README to reference processor repo

3. **Update GitHub Actions**
   - Choose Option A or B above
   - Configure secrets and permissions
   - Test with a sample transcript

4. **Update documentation**
   - README in both repos
   - Update AGENTS.md with new paths
   - Update this PRD

### Advantages

- ✅ **Clean separation**: Code and data evolve independently
- ✅ **Better git history**: Easy to see code changes vs. data changes
- ✅ **Flexible deployment**: Can run processor anywhere
- ✅ **Access control**: Different permissions for code maintainers vs. data users
- ✅ **Faster cloning**: Code repo is small and fast
- ✅ **Testing**: Can test processor against sample data without affecting production

### Trade-offs

- ❌ **Complexity**: Two repos to manage instead of one
- ❌ **Configuration**: Need to coordinate paths between repos
- ❌ **GitHub Actions**: More complex workflow with two checkouts
- ❌ **Local setup**: Users need to clone both repos

### Recommendation

**Option A (Data repo triggers processor)** is recommended because:
- Data changes are the natural trigger (new transcript in inbox)
- All logic stays in data repo's workflow
- Processor repo is truly just code (no workflows needed)
- Simpler mental model: "data repo watches inbox and calls processor"

### Implementation Tasks (Phase 4)

1. **Create processor repository**
   - Initialize new repo `meeting-notes-processor`
   - Move code files from data repo
   - Add README explaining purpose

2. **Update processor code**
   - Add `WORKSPACE_DIR` environment variable support
   - Update config.yaml with proper paths
   - Test with relative and absolute paths

3. **Update data repository**
   - Create new GitHub Actions workflow (Option A)
   - Configure secrets (GH_TOKEN)
   - Remove code files (keep data only)

4. **Documentation**
   - Update README in both repos
   - Document deployment scenarios
   - Update AGENTS.md

5. **Testing**
   - Test webhook daemon with separated repos
   - Test GitHub Actions with sample transcript
   - Verify both local and cloud scenarios work

### Open Questions (Phase 4)

1. Should the data repo workflow always check out latest processor code, or pin to a specific version/tag?
2. How to handle processor code updates - automatic or require manual data repo workflow updates?
3. Should there be a "dry run" mode for testing without committing to data repo?
4. What's the best way to handle configuration differences between local dev and production?
5. Should we support multiple data repos with one processor (e.g., personal vs. team transcripts)?
