#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "sounddevice>=0.5.0",
#     "numpy>=1.26.0",
# ]
# ///
"""
VBAN Sender — streams audio from a macOS audio device over UDP.

Captures audio from Zoom/Teams virtual audio devices (or any input device)
and sends it as VBAN protocol packets to a remote receiver.

Usage:
  uv run vban_send.py                              # list devices, then start with defaults
  uv run vban_send.py -d ZoomAudioDevice -t pilot   # stream Zoom audio to pilot
  uv run vban_send.py --list-devices                # just list available input devices

The VBAN protocol sends PCM audio in UDP packets with a 28-byte header.
Designed to pair with vban_recv.py on the transcription appliance.
"""

import argparse
import logging
import signal
import socket
import struct
import sys
import time

import numpy as np
import sounddevice as sd

# ---------------------------------------------------------------------------
# VBAN Protocol Constants
# ---------------------------------------------------------------------------

VBAN_HEADER_MAGIC = b"VBAN"
VBAN_HEADER_SIZE = 28

# Sample rate indices (VBAN specification)
VBAN_SR_TABLE = [
    6000, 12000, 24000, 48000, 96000, 192000, 384000,
    8000, 16000, 32000, 64000, 128000, 256000, 512000,
    11025, 22050, 44100, 88200, 176400, 352800, 705600,
]

# Data format
VBAN_DATATYPE_INT16 = 0x01

# Protocol / codec
VBAN_PROTOCOL_AUDIO = 0x00
VBAN_CODEC_PCM = 0x00

# Default settings
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 1
DEFAULT_PORT = 6980
DEFAULT_STREAM_NAME = "MeetingAudio"
SAMPLES_PER_PACKET = 256  # sweet spot for latency vs overhead

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [vban_send] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vban_send")

# ---------------------------------------------------------------------------
# VBAN Packet Builder
# ---------------------------------------------------------------------------


def sr_index(rate: int) -> int:
    """Look up the VBAN sample rate index."""
    try:
        return VBAN_SR_TABLE.index(rate)
    except ValueError:
        raise ValueError(
            f"Sample rate {rate} not in VBAN spec. "
            f"Supported: {VBAN_SR_TABLE}"
        )


def build_header(
    sr_idx: int,
    samples_per_frame: int,
    channels: int,
    frame_counter: int,
    stream_name: str,
) -> bytes:
    """Build a 28-byte VBAN header."""
    # Byte 4: SR index (5 bits) | protocol (3 bits)
    sr_sub_protocol = (sr_idx & 0x1F) | ((VBAN_PROTOCOL_AUDIO & 0x07) << 5)

    # Byte 5: samples per frame - 1 (0-255)
    n_samples = (samples_per_frame - 1) & 0xFF

    # Byte 6: channels - 1 (0-255)
    n_channels = (channels - 1) & 0xFF

    # Byte 7: data format (3 bits) | codec (5 bits)
    data_format = (VBAN_DATATYPE_INT16 & 0x07) | ((VBAN_CODEC_PCM & 0x1F) << 3)

    # Stream name: 16 bytes, null-padded
    name_bytes = stream_name.encode("ascii")[:16].ljust(16, b"\x00")

    # Frame counter: 4 bytes little-endian
    counter_bytes = struct.pack("<I", frame_counter & 0xFFFFFFFF)

    return (
        VBAN_HEADER_MAGIC
        + struct.pack("BBBB", sr_sub_protocol, n_samples, n_channels, data_format)
        + name_bytes
        + counter_bytes
    )


# ---------------------------------------------------------------------------
# Device Helpers
# ---------------------------------------------------------------------------


def list_input_devices():
    """List available macOS audio input devices."""
    devices = sd.query_devices()
    print("\nAvailable input devices:")
    print("-" * 60)
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            marker = " ★" if "zoom" in d["name"].lower() or "teams" in d["name"].lower() else ""
            print(
                f"  [{i}] {d['name']} "
                f"(ch:{d['max_input_channels']}, "
                f"rate:{d['default_samplerate']:.0f}){marker}"
            )
    print()


def find_device(name: str) -> int:
    """Find device index by partial name match (case-insensitive)."""
    devices = sd.query_devices()
    matches = []
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower():
            matches.append((i, d["name"]))

    if not matches:
        raise ValueError(f"No input device matching '{name}'. Run with --list-devices.")
    if len(matches) > 1:
        names = ", ".join(f"[{i}] {n}" for i, n in matches)
        raise ValueError(f"Ambiguous: multiple devices match '{name}': {names}")

    return matches[0][0]


# ---------------------------------------------------------------------------
# Main Sender Loop
# ---------------------------------------------------------------------------


def run_sender(
    target_host: str,
    target_port: int,
    device: str | int,
    sample_rate: int,
    channels: int,
    stream_name: str,
):
    """Capture audio and send VBAN packets."""
    # Resolve device
    if isinstance(device, str):
        device_idx = find_device(device)
    else:
        device_idx = device

    dev_info = sd.query_devices(device_idx)
    logger.info(f"Audio device: [{device_idx}] {dev_info['name']}")
    logger.info(f"Target: {target_host}:{target_port} stream={stream_name}")
    logger.info(f"Format: {sample_rate}Hz, {channels}ch, int16, {SAMPLES_PER_PACKET} samples/pkt")

    # Resolve target IP
    target_ip = socket.gethostbyname(target_host)
    logger.info(f"Resolved {target_host} → {target_ip}")

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sr_idx = sr_index(sample_rate)
    frame_counter = 0
    packets_sent = 0
    running = True

    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutting down...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    def audio_callback(indata, frames, time_info, status):
        nonlocal frame_counter, packets_sent
        if status:
            logger.warning(f"Audio status: {status}")
        if not running:
            raise sd.CallbackAbort

        # Convert float32 → int16
        pcm = (indata * 32767).astype(np.int16)

        # Send in chunks of SAMPLES_PER_PACKET
        offset = 0
        while offset < len(pcm):
            chunk = pcm[offset : offset + SAMPLES_PER_PACKET]
            actual_samples = len(chunk)

            header = build_header(
                sr_idx, actual_samples, channels, frame_counter, stream_name
            )
            packet = header + chunk.tobytes()

            try:
                sock.sendto(packet, (target_ip, target_port))
                frame_counter = (frame_counter + 1) & 0xFFFFFFFF
                packets_sent += 1
            except OSError as e:
                logger.error(f"Send error: {e}")

            offset += SAMPLES_PER_PACKET

    # Start audio capture
    try:
        with sd.InputStream(
            device=device_idx,
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=SAMPLES_PER_PACKET,
            callback=audio_callback,
        ):
            logger.info("🎙  Streaming... (Ctrl+C to stop)")
            last_report = time.time()
            while running:
                time.sleep(0.5)
                now = time.time()
                if now - last_report >= 10:
                    pps = packets_sent / (now - last_report)
                    logger.info(f"Stats: {packets_sent} packets sent ({pps:.0f}/s)")
                    packets_sent = 0
                    last_report = now

    except sd.PortAudioError as e:
        logger.error(f"Audio error: {e}")
        logger.error("Try running with --list-devices to see available devices")
        sys.exit(1)
    finally:
        sock.close()
        logger.info("Sender stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="VBAN audio sender — streams from macOS audio device over UDP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list-devices
  %(prog)s -d ZoomAudioDevice -t pilot
  %(prog)s -d "Microsoft Teams" -t pilot -r 48000
  %(prog)s -d 9 -t 100.64.0.5 -p 6980
        """,
    )
    parser.add_argument(
        "-d", "--device",
        default="ZoomAudioDevice",
        help="Input device name (partial match) or index (default: ZoomAudioDevice)",
    )
    parser.add_argument(
        "-t", "--target",
        default="pilot",
        help="Target hostname or IP to send VBAN stream to (default: pilot)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"UDP port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "-r", "--rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help=f"Sample rate in Hz (default: {DEFAULT_SAMPLE_RATE})",
    )
    parser.add_argument(
        "-c", "--channels",
        type=int,
        default=DEFAULT_CHANNELS,
        help=f"Number of channels (default: {DEFAULT_CHANNELS})",
    )
    parser.add_argument(
        "-s", "--stream-name",
        default=DEFAULT_STREAM_NAME,
        help=f"VBAN stream name (default: {DEFAULT_STREAM_NAME})",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available input devices and exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_devices:
        list_input_devices()
        sys.exit(0)

    # Allow device to be specified as integer index
    try:
        device = int(args.device)
    except ValueError:
        device = args.device

    run_sender(
        target_host=args.target,
        target_port=args.port,
        device=device,
        sample_rate=args.rate,
        channels=args.channels,
        stream_name=args.stream_name,
    )


if __name__ == "__main__":
    main()
