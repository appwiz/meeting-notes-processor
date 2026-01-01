#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Debug script to see copilot output in real-time.

Usage:
    uv run debug_copilot.py <transcript_file> [--workspace DIR]

This runs the same copilot command as run_summarization.py but streams
output to the terminal so you can see what's happening.
"""

import subprocess
import sys
import os
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def main():
    # Parse args
    workspace = '.'
    transcript = None
    
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == '--workspace' and i + 1 < len(args):
            workspace = args[i + 1]
            i += 2
        elif not args[i].startswith('-'):
            transcript = args[i]
            i += 1
        else:
            i += 1
    
    if not transcript:
        print("Usage: uv run debug_copilot.py <transcript_file> [--workspace DIR]")
        sys.exit(1)
    
    if not os.path.exists(transcript):
        print(f"Error: transcript file not found: {transcript}")
        sys.exit(1)
    
    # Load prompt
    workspace_prompt = os.path.join(workspace, 'prompt.txt')
    script_prompt = os.path.join(SCRIPT_DIR, 'prompt.txt')
    
    if os.path.exists(workspace_prompt):
        prompt_file = workspace_prompt
    elif os.path.exists(script_prompt):
        prompt_file = script_prompt
    else:
        print("Error: No prompt.txt found")
        sys.exit(1)
    
    print(f"Using prompt: {prompt_file}")
    
    with open(prompt_file, 'r') as f:
        prompt_template = f.read()
    
    # Use relative paths like run_summarization does
    input_relative = os.path.basename(transcript)
    output_file = "debug-output.org"
    
    # Copy transcript to workspace so copilot can read it
    if workspace != '.':
        import shutil
        dest = os.path.join(workspace, input_relative)
        shutil.copy(transcript, dest)
        print(f"Copied transcript to: {dest}")
    
    final_prompt = prompt_template.format(input_file=input_relative, output_file=output_file)
    
    print(f"\n{'='*60}")
    print("PROMPT (first 500 chars):")
    print(f"{'='*60}")
    print(final_prompt[:500])
    print(f"... ({len(final_prompt)} total chars)")
    print(f"{'='*60}\n")
    
    command = [
        'npx', '@github/copilot',
        '-p', final_prompt,
        '--allow-tool', 'write',
        '--model', 'claude-sonnet-4.5'
    ]
    
    print(f"Running: {' '.join(command[:4])} '<prompt>' {' '.join(command[5:])}")
    print(f"Working directory: {os.path.abspath(workspace)}")
    print(f"\n{'='*60}")
    print("COPILOT OUTPUT (streaming):")
    print(f"{'='*60}\n")
    
    # Stream output instead of capturing
    process = subprocess.Popen(
        command,
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1  # Line buffered
    )
    
    # Read and print output in real-time
    try:
        for line in process.stdout:
            print(line, end='', flush=True)
    except KeyboardInterrupt:
        print("\n\nInterrupted! Killing copilot process...")
        process.kill()
        sys.exit(1)
    
    process.wait()
    
    print(f"\n{'='*60}")
    print(f"Exit code: {process.returncode}")
    
    # Check if output was created
    output_path = os.path.join(workspace, output_file)
    if os.path.exists(output_path):
        print(f"Output file created: {output_path}")
        with open(output_path, 'r') as f:
            content = f.read()
        print(f"Output size: {len(content)} bytes")
    else:
        print(f"Output file NOT created: {output_path}")

if __name__ == '__main__':
    main()
