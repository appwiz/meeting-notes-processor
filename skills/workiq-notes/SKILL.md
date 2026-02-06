---
name: workiq-notes
description: "Generate org-mode meeting notes from WorkIQ. Use when the user wants meeting notes for a meeting they missed or didn't record. The agent queries WorkIQ for meeting details and generates structured org-mode notes compatible with meeting-notes-processor."
---

# WorkIQ Meeting Notes Skill

## When to Invoke

- User missed a meeting and wants notes
- User asks for notes for a meeting they didn't record
- User says "generate notes for [meeting] on [date]"

## Workflow

1. Get meeting title and date (YYYY-MM-DD) from user
2. Query WorkIQ with: `workiq-ask_work_iq`
3. Format response into org template
4. Write file using `write_note.py`

## Step 1: Query WorkIQ

```
Tell me everything you can about the "[TITLE]" meeting on [DATE]. Include:
- Who attended (list all names)
- What was discussed (main topics)
- Decisions made
- Action items (with owners if stated)
- Open questions raised
Be detailed and comprehensive.
```

If the first query fails to find the meeting, try variations:
- Search by organizer name
- Search for recordings/transcripts on that date
- Try partial title matches

## Step 2: Format Org Note

Use the EXACT format from `prompt.txt`. Key requirements:

- Title is a **second level heading** (`**`) - meaningful, include team name in brackets if applicable
- Tags: `:note:workiq:` (use `workiq` instead of `transcribed` since this is from WorkIQ)
- Timestamp on line after title: `[YYYY-MM-DD DAY HH:MM]` (use meeting start time)
- Property drawer with `:PARTICIPANTS:`, `:TOPIC:`, `:SLUG:`
- SLUG: 2-5 word hyphenated lowercase slug for filename
- Refer to participants by **full name** when known
- Actions use `- [ ]` format with owner names
- Wrap lines to 80 columns EXCEPT headings and list items
- Use hyphen `-` for bullets, NOT asterisk

```org
** [Team if known] Meeting Title :note:workiq:
[YYYY-MM-DD DAY HH:MM]
:PROPERTIES:
:PARTICIPANTS: Full Name 1, Full Name 2, Edd Wilder-James
:TOPIC: Brief topic description
:SLUG: descriptive-meeting-slug
:END:

TL;DR: One sentence summary of the meeting outcome.

*** Actions

- [ ] Full Name: Do the specific action
- [ ] Another Person: Their assigned action

*** Open questions

- First unresolved question from the meeting
- Second open question

*** Summary

Brief summary of the discussion, wrapped to 80 columns. Include key points,
decisions made, and important context. Do not include timestamps or citations.
```

Example title formats:
- `** [CIP] Product Shaping: Auto Model Selection Strategy :note:workiq:`
- `** Quality Workstream End-of-Month Update :note:workiq:`
- `** 1:1 with Sharon Lo :note:workiq:`

## Step 3: Write File

Pipe content to the helper script:

```bash
cat << 'ORGEOF' | uv run ~/git/meeting-notes-processor/skills/workiq-notes/write_note.py \
  --date "YYYY-MM-DD" --slug "meeting-slug" --title "Meeting Title"
[ORG CONTENT HERE]
ORGEOF
```

The script handles: pull --rebase → write → commit → pull --rebase → push

## Configuration

Data repo path from `config.yaml` or default `~/git/meeting-notes`
