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

```org
** [TITLE] :note:workiq:
[YYYY-MM-DD DAY]
:PROPERTIES:
:PARTICIPANTS: [from WorkIQ]
:TOPIC: [main topic]
:SLUG: [2-5 word hyphenated slug]
:SOURCE: WorkIQ (Microsoft 365 Copilot)
:END:

TL;DR: [one sentence]

*** Actions

- [ ] [action with owner]

*** Open questions

- [question]

*** Summary

[detailed summary, wrapped to 80 columns]
```

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
