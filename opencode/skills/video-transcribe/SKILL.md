---
name: video-transcribe
description: >
  Transcribe a video to text -- a YouTube URL (caption track first, then local whisper) or a local
  media file (whisper). Use when a task needs the content of a talk, lecture, demo, or recording.
  Triggers on: transcribe this video, what does this talk say, watch this video, youtube transcript,
  get the captions, summarize this recording, what's in this video.
version: 1.0.0
metadata:
  fools:
    tags: [research, media, transcription, video, audio]
    related_skills: [grounded-citations]
---

# Video transcribe

Get a video's content as text. The agent cannot watch or listen directly; it transcribes.

**Caption first.** Most YouTube videos carry an auto-generated or author caption track. Pull it with
yt-dlp -- it is fast, free, and a real transcription, no speech-to-text needed. This is the default
path; only fall back to audio transcription when no captions exist.

**Audio fallback.** If a video has no captions (or the source is a local media file), extract the
audio with yt-dlp and transcribe locally with faster-whisper (or whisper.cpp). Slower and rougher;
prefer captions when they exist.

## How to run

```
python opencode/skills/video-transcribe/scripts/transcribe.py <youtube-url-or-local-path> --out /tmp/transcript.txt
```

Then read the transcript from the returned path. Long transcripts: read in windows with `read
offset/limit`, and if the task is a research one, note() the key claims with evidence as you go.

## When to use

- A talk, lecture, conference presentation, or podcast-style video -- captions carry it well.
- A local media file (`.mp4`/`.mp3`) -- the audio path.
- NOT for a visual demo (a screen recording, a physical process) where the meaning is in the images
  -- captions won't carry it; say so plainly if that's the case.

## Prerequisites

- `yt-dlp` + `ffmpeg` on PATH (both present on the harness host).
- `faster-whisper` for the no-captions / local-file path (install into the bench venv if needed).

## Verification

A good run produces a transcript with a plausible word count for the video's length (a 40-min talk is
~5-7k words) and the opening line should read as coherent speech, not fragments. If the transcript
is empty, tiny, or garbled, the captions were absent or the audio transcription failed -- say so
rather than presenting fragments as content.
