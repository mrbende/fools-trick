#!/usr/bin/env python3
"""Transcribe a video (YouTube URL or local media file) to a text transcript.

Caption-first: if the video has a subtitle/caption track, pull it (fast, free, accurate) and clean
the VTT/SRT to plain text. If no captions exist, download the audio with yt-dlp and transcribe
locally with faster-whisper (or whisper.cpp) if available.

    python transcribe.py <url-or-path> [--out transcript.txt] [--lang en]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _clean_vtt(raw: str) -> str:
    lines, seen = [], set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:")) or "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"&\w+;", " ", line)
        if line and line not in seen:
            lines.append(line)
            seen.add(line)
    return " ".join(" ".join(lines).split())


def from_captions(url: str, lang: str, outdir: Path) -> Path | None:
    out = outdir / "cap"
    rc, _ = _run(["yt-dlp", "--skip-download", "--write-auto-subs", "--write-subs",
                  "--sub-lang", lang, "--sub-format", "vtt", "-o", f"{out}.%(ext)s", url])
    for f in outdir.glob(f"cap.{lang}*.vtt"):
        text = _clean_vtt(f.read_text(errors="ignore"))
        if len(text.split()) > 20:
            dest = outdir / "transcript.txt"
            dest.write_text(text)
            return dest
    return None


def from_audio(url: str, outdir: Path) -> Path | None:
    """Download audio and transcribe locally with faster-whisper (or whisper.cpp)."""
    audio = outdir / "audio.mp3"
    rc, _ = _run(["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(outdir / "audio.%(ext)s"), url])
    if not audio.exists():
        return None
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    model = WhisperModel("base", device="cpu")
    segments, _ = model.transcribe(str(audio))
    text = " ".join(s.text.strip() for s in segments)
    dest = outdir / "transcript.txt"
    dest.write_text(text)
    return dest if text.strip() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="YouTube URL or local media path")
    ap.add_argument("--out", default=None)
    ap.add_argument("--lang", default="en")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        outdir = Path(td)
        dest = None
        # local media file: skip captions, go straight to audio transcription
        if Path(args.source).exists():
            dest = from_audio_local(Path(args.source), outdir)
        else:
            dest = from_captions(args.source, args.lang, outdir) or from_audio(args.source, outdir)
        if not dest:
            print("no captions and no local transcription engine (install faster-whisper)", file=sys.stderr)
            return 1
        text = dest.read_text()
        if args.out:
            Path(args.out).write_text(text)
        print(f"transcribed {len(text.split())} words -> {args.out or '(stdout)'}")
        if not args.out:
            print(text)
    return 0


def from_audio_local(path: Path, outdir: Path) -> Path | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    model = WhisperModel("base", device="cpu")
    segments, _ = model.transcribe(str(path))
    text = " ".join(s.text.strip() for s in segments)
    dest = outdir / "transcript.txt"
    dest.write_text(text)
    return dest if text.strip() else None


if __name__ == "__main__":
    sys.exit(main())
