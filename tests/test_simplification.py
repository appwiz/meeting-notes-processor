"""Regression coverage for calendar, quarantine, and installed-service behavior."""

import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_summarization


@pytest.mark.parametrize('age,expected_context', [(60, True), (86400, False)])
def test_cli_uses_generated_calendar_without_git_inference(tmp_path, monkeypatch, age, expected_context):
    git(tmp_path, 'init', '-q')
    calendar = tmp_path / 'calendar.org'
    calendar.write_text('* Current meeting\n')
    published = calendar.stat().st_mtime - age
    os.utime(calendar, (published, published))
    monkeypatch.setenv('CALENDAR_PATH', str(calendar))
    monkeypatch.setattr(sys, 'argv', ['run_summarization.py', '--workspace', str(tmp_path)])
    monkeypatch.setattr(run_summarization, 'load_prompt_template', lambda *_: 'prompt')
    captured = {}

    def process(*args, **kwargs):
        captured.update(kwargs)
        return 0, 0

    monkeypatch.setattr(run_summarization, 'process_inbox', process)
    with pytest.raises(SystemExit) as exc:
        run_summarization.run_summarization()

    assert exc.value.code == 2
    assert captured['calendar_path'] == (str(calendar) if expected_context else None)


def git(workspace, *args):
    return subprocess.run(
        ['git', '-C', str(workspace), *args],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


@pytest.mark.parametrize('tracked', [False, True])
def test_quarantine_preserves_content_and_unrelated_staged_work(tmp_path, tracked):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.com')
    git(tmp_path, 'config', 'core.hooksPath', '/dev/null')
    (tmp_path / 'inbox').mkdir()
    source = tmp_path / 'inbox' / 'meeting.txt'
    source.write_text('Mm-hmm.\n' * 100)
    expected = source.read_bytes()
    (tmp_path / 'unrelated.txt').write_text('initial')
    git(tmp_path, 'add', 'unrelated.txt', *(('inbox/meeting.txt',) if tracked else ()))
    git(tmp_path, 'commit', '-qm', 'Initial')
    (tmp_path / 'unrelated.txt').write_text('user edit')
    git(tmp_path, 'add', 'unrelated.txt')

    result = run_summarization.process_inbox(
        run_summarization.get_workspace_paths(str(tmp_path)), use_git=True,
    )

    assert result == (0, 0)
    assert not source.exists()
    assert (tmp_path / 'quarantine' / 'meeting.txt').read_bytes() == expected
    assert 'speech diversity' in (tmp_path / 'quarantine' / 'meeting.txt.reason.txt').read_text()
    assert git(tmp_path, 'diff', '--cached', '--name-only') == 'unrelated.txt'
    assert git(tmp_path, 'show', 'HEAD:unrelated.txt') == 'initial'


def test_quarantine_keeps_previous_rejection(tmp_path):
    (tmp_path / 'quarantine').mkdir()
    previous = tmp_path / 'quarantine' / 'meeting.txt'
    previous.write_text('previous')
    source = tmp_path / 'meeting.txt'
    source.write_text('new')

    run_summarization.quarantine_transcript(str(source), str(tmp_path), 'reason', False)

    assert previous.read_text() == 'previous'
    assert (tmp_path / 'quarantine' / 'meeting-1.txt').read_text() == 'new'


def test_quarantine_git_failure_is_not_success(tmp_path):
    (tmp_path / 'inbox').mkdir()
    source = tmp_path / 'inbox' / 'short.txt'
    source.write_text('short')

    result = run_summarization.process_inbox(
        run_summarization.get_workspace_paths(str(tmp_path)), use_git=True,
    )

    assert result == (0, 1)
    assert source.read_text() == 'short'

def test_quarantine_commit_failure_preserves_moved_source(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.com')
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    git(tmp_path, 'config', 'core.hooksPath', str(hooks))
    (tmp_path / 'inbox').mkdir()
    source = tmp_path / 'inbox' / 'short.txt'
    source.write_text('short')

    result = run_summarization.process_inbox(
        run_summarization.get_workspace_paths(str(tmp_path)), use_git=True,
    )

    assert result == (0, 1)
    assert (tmp_path / 'quarantine' / 'short.txt').read_text() == 'short'


@pytest.mark.parametrize('calendar_time,expected', [
    (None, 'unavailable'),
    ('09:00-10:00', 'context-provided'),
    ('15:00-16:00', 'unavailable'),
])
def test_note_calendar_status_and_time_matching(tmp_path, monkeypatch, calendar_time, expected):
    paths = run_summarization.get_workspace_paths(str(tmp_path))
    for key in ('inbox', 'transcripts', 'notes'):
        Path(paths[key]).mkdir()
    source = Path(paths['inbox']) / '20261001-meeting.txt'
    source.write_text(
        '---\nmeeting_start: 2026-10-01T09:05:00-07:00\n'
        'meeting_end: 2026-10-01T09:25:00-07:00\n---\nConversation.'
    )
    calendar = tmp_path / 'calendar.org'
    if calendar_time:
        calendar.write_text(f'* Scheduled identity <2026-10-01 Thu {calendar_time}>\n')
    captured = {}

    class Completed:
        returncode = 0
        stdout = []

        def poll(self):
            return 0

        def wait(self):
            return 0

    def generate(command, **kwargs):
        assert command[command.index('--model') + 1] == 'gpt-6.1-sol'
        prompt = command[command.index('-p') + 1]
        captured['prompt'] = prompt
        output = re.search(r'temp-\S+\.org', prompt).group()
        (tmp_path / output).write_text(
            '** Test note\n:PROPERTIES:\n:SLUG: test-note\n'
            ':CALENDAR_STATUS: hallucinated\n:END:\nSummary.\n'
        )
        return Completed()

    monkeypatch.setattr(run_summarization.subprocess, 'Popen', generate)
    ok, transcript, note = run_summarization.process_transcript(
        str(source), paths, prompt_template='Read {input_file}; write {output_file}',
        calendar_path=str(calendar) if calendar_time else None,
    )

    assert ok
    assert Path(transcript).read_text().endswith('Conversation.')
    note_text = Path(note).read_text()
    assert f':CALENDAR_STATUS: {expected}' in note_text
    assert note_text.count(':CALENDAR_STATUS:') == 1
    if expected == 'unavailable':
        assert 'have not been verified' in note_text
        assert 'Scheduled identity' not in captured['prompt']
    else:
        assert 'Scheduled identity' in captured['prompt']
        assert 'invitation does not prove attendance' in captured['prompt']


@pytest.mark.parametrize('target,label', [
    ('meeting-bar-restart', 'com.meeting-bar'),
    ('local-transcriber-restart', 'com.transcriber.local'),
])
def test_restart_preserves_installed_plist(tmp_path, target, label):
    agents = tmp_path / 'Library' / 'LaunchAgents'
    agents.mkdir(parents=True)
    plist = agents / f'{label}.plist'
    plist.write_bytes(b'installed custom configuration, not the repository template')
    original = plist.read_bytes()
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    for command, body in (
        ('sleep', 'exit 0'),
        ('launchctl', f'echo "{label}"'),
    ):
        executable = binaries / command
        executable.write_text('#!/bin/sh\n' + body + '\n')
        executable.chmod(0o755)

    subprocess.run(
        ['make', '-C', str(Path(__file__).resolve().parents[1] / 'transcriber'), target],
        env={**os.environ, 'HOME': str(tmp_path), 'PATH': f"{binaries}:{os.environ['PATH']}"},
        check=True, capture_output=True, text=True,
    )

    assert plist.read_bytes() == original


@pytest.mark.parametrize('target', ['meeting-bar-restart', 'local-transcriber-restart'])
def test_restart_requires_installation_without_stopping_service(tmp_path, target):
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    marker = tmp_path / 'launchctl-called'
    executable = binaries / 'launchctl'
    executable.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    executable.chmod(0o755)

    result = subprocess.run(
        ['make', '-C', str(Path(__file__).resolve().parents[1] / 'transcriber'), target],
        env={**os.environ, 'HOME': str(tmp_path), 'PATH': f"{binaries}:{os.environ['PATH']}"},
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert 'install first' in result.stdout
    assert not marker.exists()
