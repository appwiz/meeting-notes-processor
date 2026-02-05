#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyyaml>=6.0.0",
# ]
# ///
"""
Write org-mode meeting note to data repo with git operations.

Reads org content from stdin, writes to notes/ directory, and handles git:
  pull --rebase → write file → add → commit → pull --rebase → push

Usage:
    cat note.org | uv run write_note.py --date 2026-02-04 --slug meeting-slug --title "Meeting Title"
    
    # Or with heredoc:
    cat << 'EOF' | uv run write_note.py --date 2026-02-04 --slug my-meeting --title "My Meeting"
    ** My Meeting :note:workiq:
    [2026-02-04 Tue]
    ...
    EOF

Options:
    --date      Meeting date YYYY-MM-DD (used for filename prefix)
    --slug      URL-friendly slug (used for filename)
    --title     Meeting title (used for commit message)
    --workspace Path to data repo (default: from config.yaml or ~/git/meeting-notes)
    --no-push   Commit but don't push
    --dry-run   Print what would be written without writing
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent.absolute()
PROCESSOR_DIR = SCRIPT_DIR.parent.parent  # meeting-notes-processor root


def load_config() -> dict:
    """Load configuration from meeting-notes-processor config.yaml."""
    config_path = PROCESSOR_DIR / "config.yaml"
    if config_path.exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


def get_data_repo(config: dict, workspace_arg: str | None) -> Path:
    """Determine the data repo path."""
    if workspace_arg:
        return Path(workspace_arg).expanduser().absolute()
    
    data_repo = config.get('data_repo')
    if data_repo:
        p = Path(data_repo)
        if not p.is_absolute():
            p = PROCESSOR_DIR / data_repo
        return p.absolute()
    
    # Default fallback
    return Path.home() / "git" / "meeting-notes"


def run_git(args: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a git command."""
    return subprocess.run(
        ['git', *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def git_sync_and_commit(data_repo: Path, filepath: Path, title: str, push: bool = True) -> tuple[bool, str]:
    """
    Git workflow: pull --rebase, add, commit, pull --rebase, push.
    Returns (success, message).
    """
    messages = []
    
    # Initial pull --rebase
    result = run_git(['pull', '--rebase', 'origin', 'main'], data_repo)
    if result.returncode != 0:
        # Check if it's just "already up to date" or similar non-error
        if "Already up to date" not in result.stdout and "Already up to date" not in result.stderr:
            return False, f"Initial pull --rebase failed: {result.stderr.strip()}"
    messages.append("Pulled")
    
    # Add the file
    rel_path = filepath.relative_to(data_repo)
    result = run_git(['add', str(rel_path)], data_repo)
    if result.returncode != 0:
        return False, f"git add failed: {result.stderr.strip()}"
    
    # Commit
    commit_msg = f"Add WorkIQ note: {title}"
    result = run_git(['commit', '-m', commit_msg], data_repo)
    if result.returncode != 0:
        # Check if nothing to commit
        if "nothing to commit" in result.stdout or "nothing to commit" in result.stderr:
            return True, "Nothing to commit (file unchanged)"
        return False, f"git commit failed: {result.stderr.strip()}"
    messages.append("Committed")
    
    if not push:
        return True, " → ".join(messages) + " (push skipped)"
    
    # Pull --rebase again before push (in case of concurrent changes)
    result = run_git(['pull', '--rebase', 'origin', 'main'], data_repo)
    if result.returncode != 0:
        if "Already up to date" not in result.stdout and "Already up to date" not in result.stderr:
            return False, f"Pre-push pull --rebase failed: {result.stderr.strip()}"
    
    # Push
    result = run_git(['push', 'origin', 'main'], data_repo, timeout=120)
    if result.returncode != 0:
        return False, f"git push failed: {result.stderr.strip()}"
    messages.append("Pushed")
    
    return True, " → ".join(messages)


def main():
    parser = argparse.ArgumentParser(
        description="Write org-mode meeting note to data repo with git operations"
    )
    parser.add_argument(
        '--date', '-d',
        required=True,
        help='Meeting date in YYYY-MM-DD format (for filename)'
    )
    parser.add_argument(
        '--slug', '-s',
        required=True,
        help='URL-friendly slug (for filename)'
    )
    parser.add_argument(
        '--title', '-t',
        required=True,
        help='Meeting title (for commit message)'
    )
    parser.add_argument(
        '--workspace', '-w',
        help='Path to data repo (default: from config.yaml or ~/git/meeting-notes)'
    )
    parser.add_argument(
        '--no-push',
        action='store_true',
        help='Commit but do not push'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print what would be written without writing'
    )
    
    args = parser.parse_args()
    
    # Validate date format
    try:
        datetime.strptime(args.date, '%Y-%m-%d')
    except ValueError:
        print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.", file=sys.stderr)
        sys.exit(1)
    
    # Read content from stdin
    if sys.stdin.isatty():
        print("Error: No content provided. Pipe org content to stdin.", file=sys.stderr)
        print("Example: cat note.org | uv run write_note.py --date 2026-02-04 --slug my-meeting --title 'My Meeting'", file=sys.stderr)
        sys.exit(1)
    
    content = sys.stdin.read()
    if not content.strip():
        print("Error: Empty content provided.", file=sys.stderr)
        sys.exit(1)
    
    # Ensure content ends with newline
    if not content.endswith('\n'):
        content += '\n'
    
    # Load config and determine data repo
    config = load_config()
    data_repo = get_data_repo(config, args.workspace)
    notes_dir = data_repo / 'notes'
    
    # Generate filename
    date_prefix = args.date.replace('-', '')[:8]
    filename = f"{date_prefix}-{args.slug}.org"
    filepath = notes_dir / filename
    
    if args.dry_run:
        print(f"Would write to: {filepath}")
        print(f"Content ({len(content)} bytes):")
        print("---")
        print(content)
        print("---")
        sys.exit(0)
    
    # Validate paths
    if not data_repo.exists():
        print(f"Error: Data repo not found: {data_repo}", file=sys.stderr)
        sys.exit(1)
    if not notes_dir.exists():
        print(f"Error: Notes directory not found: {notes_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Check if file exists
    if filepath.exists():
        print(f"Warning: Overwriting existing file: {filepath}", file=sys.stderr)
    
    # Write the file
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Wrote: {filepath}")
    
    # Git operations
    success, message = git_sync_and_commit(
        data_repo, 
        filepath, 
        args.title,
        push=not args.no_push
    )
    
    if success:
        print(f"Git: {message}")
    else:
        print(f"Git error: {message}", file=sys.stderr)
        sys.exit(1)
    
    print("Done!")


if __name__ == '__main__':
    main()
