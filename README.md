# Yout local (yt-dlp backend)

Requirements: Python 3.9+, `yt-dlp`, `ffmpeg` (on PATH). No other Python packages.

    pip install -U yt-dlp
    python3 server.py            # → http://127.0.0.1:8000

Options: `--port 9000`, env `YTDLP=/path/to/yt-dlp`, `YTDLP_ARGS="--cookies-from-browser chrome"`
(useful if YouTube asks for sign-in), `YOUT_DOWNLOADS=/some/folder`.

By default only the browser download is kept (the server-side copy is removed once the browser has the whole file). Use `--keep-copy` to also keep it in `downloads/` (the التحميلات page then lists it). The server listens on 127.0.0.1 only.

## What each control does
| Control | Behaviour |
|---|---|
| Search box | URL → converter page; text → YouTube search (with suggestions) |
| Slider + time boxes | Clip start/end (yt-dlp `--download-sections`) |
| MP3 / MP4 / WAV / GIF | Output format (ffmpeg) |
| Quality | MP3 kbit/s · MP4 resolution (from available formats) · WAV sample rate · GIF width |
| H.264 | MP4 compatible with Mac/iOS (re-encodes only if the source isn't H.264) |
| Title / Artist | ID3/MP4 tags + file name |
| Remove silence / Normalize | ffmpeg `silenceremove` / `loudnorm` |
| Discover metadata | Artist/title guessed from video info + cover art embedded (MP3) |
| Subtitles + language | Downloaded (manual or auto) and embedded in MP4 |
| التحميلات | Lists / deletes saved files |
