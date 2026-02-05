#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "sounddevice>=0.5.0",
#     "numpy>=1.26.0",
# ]
# ///
"""
VBAN Receiver — receives VBAN UDP audio and plays it to an output device.

Listens for VBAN packets from the laptop's vban_send.py and outputs
audio to BlackHole 2ch (or any output device), where the transcriber's
ffmpeg can record it.

Usage:
  uv run vban_recv.py                              # receive on default port, output to BlackHole 2ch
  uv run vban_recv.py -d "BlackHole 2ch" -p 6980   # explicit device and port
  uv run vban_recv.py --list-devices                # list output devices

Designed to run as a launchd service on the transcription appliance.
"""

import argparse
import logging
import signal
import socket
import struct
import sys
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

# ---------------------------------------------------------------------------
# VBAN Protocol Constants
# ---------------------------------------------------------------------------

VBAN_HEADER_MAGIC = b"VBAN"
VBAN_HEADER_SIZE = 28

VBAN_SR_TABLE = [
    6000, 12000, 24000, 48000, 96000, 192000, 384000,
    8000, 16000, 32000, 64000, 128000, 256000, 512000,
    11025, 22050, 44100, 88200, 176400, 352800, 705600,
]

VBAN_PROTOCOL_AUDIO = 0x00
VBAN_DATATYPE_INT16 = 0x01

# Defaults
DEFAULT_PORT = 6980
DEFAULT_DEVICE = "BlackHole 2ch"
DEFAULT_STREAM_NAME = "MeetingAudio"
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 1

# Buffer: ~200ms of audio at 48kHz to smooth jitter
BUFFER_TARGET_FRAMES = 9600
BUFFER_MAX_FRAMES = 48000  # 1 second max

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [vban_recv] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vban_recv")

# ---------------------------------------------------------------------------
# VBAN Packet Parser
# ---------------------------------------------------------------------------


def parse_header(data: bytes) -> dict | None:
    """Parse a VBAN packet header. Returns None if invalid."""
    if len(data) < VBAN_HEADER_SIZE:
        return None
    if data[:4] != VBAN_HEADER_MAGIC:
        return None

    sr_sub = data[4]
    sr_index = sr_sub & 0x1F
    protocol = (sr_sub >> 5) & 0x07

    if protocol != VBAN_PROTOCOL_AUDIO:
        return None

    n_samples = data[5] + 1
    n_channels = data[6] + 1
    data_format = data[7] & 0x07
    codec = (data[7] >> 3) & 0x1F

    stream_name = data[8:24].split(b"\x00")[0].decode("ascii", errors="replace")
    frame_counter = struct.unpack("<I", data[24:28])[0]

    sample_rate = VBAN_SR_TABLE[sr_index] if sr_index < len(VBAN_SR_TABLE) else 0

    return {
        "sample_rate": sample_rate,
        "samples": n_samples,
        "channels": n_channels,
        "format": data_format,
        "codec": codec,
        "stream_name": stream_name,
        "frame_counter": frame_counter,
    }


# ---------------------------------------------------------------------------
# Device Helpers
# ---------------------------------------------------------------------------


def list_output_devices():
    """List available macOS audio output devices."""
    devices = sd.query_devices()
    print("\nAvailable output devices:")
    print("-" * 60)
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0:
            marker = " ★" if "blackhole" in d["name"].lower() else ""
            print(
                f"  [{i}] {d['name']} "
                f"(ch:{d['max_output_channels']}, "
                f"rate:{d['default_samplerate']:.0f}){marker}"
            )
    print()


def find_device(name: str) -> int:
    """Find output device index by partial name match."""
    devices = sd.query_devices()
    matches = []
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0 and name.lower() in d["name"].lower():
            matches.append((i, d["name"]))

    if not matches:
        raise ValueError(f"No output device matching '{name}'. Run with --list-devices.")
    if len(matches) > 1:
        names = ", ".join(f"[{i}] {n}" for i, n in matches)
        raise ValueError(f"Ambiguous: multiple devices match '{name}': {names}")

    return matches[0][0]


# ---------------------------------------------------------------------------
# Main Receiver Loop
# ---------------------------------------------------------------------------


def run_receiver(
    listen_port: int,
    device: str | int,
    stream_name: str,
    sample_rate: int,
    channels: int,
):
    """Listen for VBAN packets and play audio to output device."""
    # Resolve device
    if isinstance(device, str):
        device_idx = find_device(device)
    else:
        device_idx = device

    dev_info = sd.query_devices(device_idx)
    output_channels = dev_info["max_output_channels"]
    logger.info(f"Output device: [{device_idx}] {dev_info['name']} ({output_channels}ch native)")
    logger.info(f"Listening on UDP port {listen_port} for stream '{stream_name}'")
    logger.info(f"Format: {sample_rate}Hz, VBAN mono → {output_channels}ch output")

    # Audio buffer (thread-safe deque of numpy arrays)
    audio_buffer: deque[np.ndarray] = deque(maxlen=1000)
    buffer_frames = 0
    buffer_lock = threading.Lock()

    running = True
    receiving = False
    packets_received = 0
    last_packet_time = 0.0

    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Shutting down...")
        running = False

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Audio output callback — pulls mono buffer data and expands to device channels
    def audio_callback(outdata, frames, time_info, status):
        nonlocal buffer_frames, receiving
        if status:
            logger.warning(f"Output status: {status}")

        filled = 0
        with buffer_lock:
            while filled < frames and audio_buffer:
                chunk = audio_buffer[0]
                needed = frames - filled
                if len(chunk) <= needed:
                    # chunk is 1D mono — reshape to (N,1) then broadcast to all channels
                    mono_col = chunk.reshape(-1, 1)
                    outdata[filled : filled + len(chunk)] = np.broadcast_to(
                        mono_col, (len(chunk), output_channels)
                    )
                    filled += len(chunk)
                    audio_buffer.popleft()
                    buffer_frames -= len(chunk)
                else:
                    mono_col = chunk[:needed].reshape(-1, 1)
                    outdata[filled : filled + needed] = np.broadcast_to(
                        mono_col, (needed, output_channels)
                    )
                    audio_buffer[0] = chunk[needed:]
                    buffer_frames -= needed
                    filled = frames

        # Zero-fill any remaining
        if filled < frames:
            outdata[filled:] = 0

    # Start audio output stream — use device's native channel count
    output_stream = sd.OutputStream(
        device=device_idx,
        samplerate=sample_rate,
        channels=output_channels,
        dtype="float32",
        blocksize=256,
        callback=audio_callback,
    )

    # UDP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", listen_port))
    sock.settimeout(1.0)  # 1s timeout for clean shutdown

    try:
        output_stream.start()
        logger.info("🔊 Waiting for VBAN stream... (Ctrl+C to stop)")

        last_report = time.time()

        while running:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                # Check for stale stream
                if receiving and time.time() - last_packet_time > 5:
                    logger.info("Stream idle for 5s, waiting for packets...")
                    receiving = False
                continue

            header = parse_header(data)
            if header is None:
                continue

            # Filter by stream name
            if header["stream_name"] != stream_name:
                logger.debug(f"Ignoring stream '{header['stream_name']}' from {addr}")
                continue

            if not receiving:
                logger.info(f"Receiving from {addr[0]}:{addr[1]} — {header['sample_rate']}Hz, {header['channels']}ch")
                receiving = True

            packets_received += 1
            last_packet_time = time.time()

            # Extract PCM data after header
            pcm_data = data[VBAN_HEADER_SIZE:]
            if not pcm_data:
                continue

            # Convert int16 → float32
            samples = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32767.0

            with buffer_lock:
                # Drop oldest if buffer is too full (avoid runaway latency)
                while buffer_frames > BUFFER_MAX_FRAMES:
                    dropped = audio_buffer.popleft()
                    buffer_frames -= len(dropped)

                audio_buffer.append(samples)
                buffer_frames += len(samples)

            # Periodic stats
            now = time.time()
            if now - last_report >= 10:
                logger.info(
                    f"Stats: {packets_received} pkts recv, "
                    f"buffer: {buffer_frames} frames ({buffer_frames / sample_rate * 1000:.0f}ms)"
                )
                packets_received = 0
                last_report = now

    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
    finally:
        output_stream.stop()
        output_stream.close()
        sock.close()
        logger.info("Receiver stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="VBAN audio receiver — plays incoming VBAN stream to output device",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list-devices
  %(prog)s -d "BlackHole 2ch"
  %(prog)s -d "BlackHole 2ch" -p 6980 -s MeetingAudio
        """,
    )
    parser.add_argument(
        "-d", "--device",
        default=DEFAULT_DEVICE,
        help=f"Output device name (partial match) or index (default: {DEFAULT_DEVICE})",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"UDP port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "-r", "--rate",
        type=int,
        default=DEFAULT_SAMPLE_RATE,
        help=f"Expected sample rate (default: {DEFAULT_SAMPLE_RATE})",
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
        help=f"VBAN stream name to accept (default: {DEFAULT_STREAM_NAME})",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List output devices and exit",
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
        list_output_devices()
        sys.exit(0)

    # Allow device to be specified as integer index
    try:
        device = int(args.device)
    except ValueError:
        device = args.device

    run_receiver(
        listen_port=args.port,
        device=device,
        stream_name=args.stream_name,
        sample_rate=args.rate,
        channels=args.channels,
    )


if __name__ == "__main__":
    main()
