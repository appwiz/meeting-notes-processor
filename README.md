# Meeting Notes Knowledge Base

Automatically process meeting transcripts into organized, searchable org-mode summaries using AI.

## Overview

This system ingests meeting transcripts (from MacWhisper, Zoom, Teams, etc.), generates AI-powered summaries in org-mode format, and maintains a structured archive with meaningful filenames based on content.

**Note:** This project supports both single-repository and separated-repository architectures. See "Repository Architecture" section below for details.

## Quick Start

### Same-Repository Setup (Default)

1. **Install dependencies:**
   ```bash
   npm install
   ```

2. **Add transcripts to inbox:**
   ```bash
   cp your-transcript.txt inbox/
   ```

3. **Process transcripts:**
   ```bash
   uv run run_summarization.py
   ```

   Options:
   - `--target copilot` (default) or `--target gemini` - Choose LLM backend
   - `--model MODEL_NAME` - Specify custom model
   - `--git` - Perform automated git operations (for CI/CD)

### Webhook Daemon (MacWhisper Integration)

MacWhisper can send transcripts directly to a local webhook daemon:

```bash
# Run the daemon
uv run webhook_daemon.py

# With git operations enabled
GITHUB_TOKEN=xxx uv run webhook_daemon.py
```

Configure MacWhisper to send webhooks to: `http://localhost:9876/webhook`

The daemon will:
1. Receive transcript via HTTP POST
2. Write to `inbox/` with timestamped filename
3. Optionally commit and push to trigger automated processing

See [config.yaml](config.yaml) for webhook daemon configuration.

## Repository Architecture

This project supports two deployment models:

### Option 1: Same Repository (Default)

Code and data live together in one repository. Simple setup for personal use.

```
meeting-notes/
├── run_summarization.py
├── webhook_daemon.py
├── inbox/
├── transcripts/
└── notes/
```

### Option 2: Separated Repositories

**Recommended for production:** Code and data in separate repositories.

**Processor Repository** (`meeting-notes-processor`):
- `run_summarization.py`
- `webhook_daemon.py`
- `config.yaml`
- `.github/workflows/`

**Data Repository** (`meeting-notes`):
- `inbox/`
- `transcripts/`
- `notes/`

**Setup:**

1. **Clone both repositories side-by-side:**
   ```bash
   git clone https://github.com/ewilderj/meeting-notes-processor.git
   git clone https://github.com/ewilderj/meeting-notes.git
   ```

2. **Configure webhook daemon** (in processor repo):
   ```yaml
   # config.yaml
   directories:
     inbox: ../meeting-notes/inbox
     repository: ../meeting-notes
   
   git:
     repository_url: "github.com/ewilderj/meeting-notes.git"
   ```

3. **Set up GitHub Actions** in data repo with workflow from `.github/workflows/process-transcripts-separated.yml`

4. **Run processor commands with WORKSPACE_DIR:**
   ```bash
   cd meeting-notes-processor
   WORKSPACE_DIR=../meeting-notes uv run run_summarization.py
   ```

**Advantages:**
- Clean separation of concerns
- Independent version history for code and data
- Different access controls possible
- Faster code repository cloning

### Automated Processing (GitHub Actions)

When you commit a transcript file to `inbox/`, GitHub Actions automatically:
1. Processes the transcript with AI summarization
2. Generates a meaningful slug from content
3. Creates organized files in `transcripts/` and `notes/`
4. Commits the results back to the repository

**Setup:** Add `GH_TOKEN` secret with repository Contents write permission.

For separated repos, see `.github/workflows/process-transcripts-separated.yml` workflow.

## Directory Structure

```
inbox/           # Drop transcripts here (.txt, .md)
transcripts/     # Processed original transcripts (YYYYMMDD-slug.txt)
notes/          # AI-generated org-mode summaries (YYYYMMDD-slug.org)
examples/       # Example transcripts for testing
```

## Output Format

Each transcript generates two files:
- `YYYYMMDD-slug.txt` - Original transcript in `transcripts/`
- `YYYYMMDD-slug.org` - Org-mode summary in `notes/` with:
  - TL;DR
  - Action items
  - Open questions
  - Discussion summary
  - Metadata (participants, topic, slug)

## Requirements

- Python 3.11+ with `uv` package manager
- Node.js 22+
- GitHub Copilot CLI (`@github/copilot`) or Gemini CLI (`@google/gemini-cli`)

## Documentation

See [PRD.md](PRD.md) for detailed requirements and implementation phases.
