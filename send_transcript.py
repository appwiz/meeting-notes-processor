#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests>=2.31.0",
# ]
# ///
"""
Test script for webhook daemon

Sends a transcript file to the webhook daemon for processing.

Usage:
    uv run send_transcript.py <transcript_file>
    uv run send_transcript.py -h myhost:1234 <transcript_file>

Example:
    uv run send_transcript.py examples/q1-planning-sarah.txt
    uv run send_transcript.py -h localhost:9999 examples/q1-planning-sarah.txt
"""

import sys
import os
import argparse
import requests
import json

def send_to_webhook(filepath, webhook_url="http://localhost:9876/webhook"):
    """Send a transcript file to the webhook daemon."""
    
    # Check if file exists
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        return False
    
    # Read the transcript
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            transcript = f.read()
    except Exception as e:
        print(f"Error reading file: {e}")
        return False
    
    # Extract title: skip YAML front matter if present, use first content line
    lines = transcript.strip().split('\n')
    if lines:
        title_line = 0
        # Skip YAML front matter (--- ... ---)
        if lines[0].strip() == '---':
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == '---':
                    title_line = i + 1
                    break
        # Find first non-empty line after any front matter
        while title_line < len(lines) and not lines[title_line].strip():
            title_line += 1
        if title_line < len(lines):
            title = lines[title_line].strip()
        else:
            title = os.path.splitext(os.path.basename(filepath))[0]
    else:
        title = os.path.basename(filepath)
    
    # Prepare payload
    payload = {
        'title': title,
        'transcript': transcript
    }
    
    # Send to webhook
    print(f"Sending to webhook: {webhook_url}")
    print(f"Title: {title}")
    print(f"Transcript size: {len(transcript)} bytes")
    print()
    
    try:
        response = requests.post(
            webhook_url,
            json=payload,
            headers={'Content-Type': 'application/json'}
        )
        
        print(f"Response status: {response.status_code}")
        print(f"Response body:")
        print(json.dumps(response.json(), indent=2))
        
        return response.status_code == 200
        
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to webhook daemon.")
        print("Make sure it's running: uv run meetingnotesd.py")
        return False
    except Exception as e:
        print(f"Error sending request: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Send a transcript file to the webhook daemon for processing.",
        add_help=False  # Disable default -h so we can use it for host
    )
    parser.add_argument(
        '-h', '--host',
        metavar='HOST:PORT',
        default='localhost:9876',
        help='Host and port to send to (default: localhost:9876)'
    )
    parser.add_argument(
        '--help',
        action='help',
        help='Show this help message and exit'
    )
    parser.add_argument(
        'transcript_file',
        help='Path to the transcript file to send'
    )
    
    args = parser.parse_args()
    
    webhook_url = f"http://{args.host}/webhook"
    success = send_to_webhook(args.transcript_file, webhook_url)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
