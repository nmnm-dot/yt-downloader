#!/usr/bin/env python3
"""
Local backend for the Yout-style UI. Only needs: Python 3.9+, yt-dlp, ffmpeg.

    python3 server.py                 # http://127.0.0.1:8000
    python3 server.py --port 9000
    YTDLP=/path/to/yt-dlp python3 server.py
    YTDLP_ARGS="--cookies-from-browser chrome" python3 server.py

No third-party Python packages are used (standard library only).
"""
import argparse
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
DOWNLOADS = Path(os.environ.get("YOUT_DOWNLOADS", ROOT / "downloads")).resolve()
TMP = DOWNLOADS / ".tmp"

MAX_PARALLEL_JOBS = 2
MAX_GIF_SECONDS = 120
INFO_TTL = 600

AUDIO_QUALITIES = {"mp3": {32, 64, 128, 192, 256, 320}}
WAV_RATES = {22050, 44100, 48000}
GIF_WIDTHS = {240, 320, 480, 640}
MP4_HEIGHTS = {144, 240, 360, 480, 720, 1080, 1440, 2160, 4320}

# --------------------------------------------------------------------------- tools

def ytdlp_cmd():
    exe = os.environ.get("YTDLP") or shutil.which("yt-dlp")
    if exe:
        base = [exe]
    else:
        try:
            import yt_dlp  # noqa: F401
            base = [sys.executable, "-m", "yt_dlp"]
        except ImportError:
            raise RuntimeError("yt-dlp not found. Install it: pip install -U yt-dlp (or set YTDLP=/path)")
    extra = shlex.split(os.environ.get("YTDLP_ARGS", ""))
    return base + extra


def ffmpeg_bin():
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found. Install ffmpeg and make sure it is on PATH.")
    return exe


def ffprobe_bin():
    exe = os.environ.get("FFPROBE") or shutil.which("ffprobe")
    return exe


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def clean_url(raw):
    raw = (raw or "").strip()
    u = urlparse(raw)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ApiError("الرابط غير صالح (لازم يبدأ بـ http:// أو https://)")
    return raw


def run_capture(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ApiError("انتهت مهلة yt-dlp، جرّب مرة تانية", 504)
    return p


def last_error_line(text):
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    errs = [l for l in lines if l.startswith("ERROR")]
    msg = (errs or lines or ["unknown error"])[-1]
    msg = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?", "", msg)
    return msg[:400]

# --------------------------------------------------------------------------- info

_info_cache = {}
_info_lock = threading.Lock()


def hms(seconds):
    seconds = int(round(seconds or 0))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def thumb_for(entry):
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    th = entry.get("thumbnails") or []
    if th:
        return th[-1].get("url")
    eid, ie = entry.get("id"), (entry.get("ie_key") or entry.get("extractor_key") or "").lower()
    if eid and ie == "youtube":
        return f"https://i.ytimg.com/vi/{eid}/mqdefault.jpg"
    return None


def discover_meta(info):
    """Best-effort artist / title like the original 'اكتشف البيانات الوصفية' option."""
    title = info.get("track") or info.get("title") or ""
    artist = info.get("artist") or info.get("creator") or ""
    if isinstance(artist, list):
        artist = ", ".join(artist)
    if not artist:
        m = re.match(r"^\s*(.+?)\s+[-–—]\s+(.+?)\s*$", title)
        if m and not info.get("track"):
            artist, title = m.group(1), m.group(2)
    if not artist:
        artist = info.get("uploader") or info.get("channel") or ""
        artist = re.sub(r"\s*-\s*Topic$", "", artist)
    return {"title": title.strip(), "artist": str(artist).strip()}


def entry_summary(e):
    url = e.get("webpage_url") or e.get("url") or ""
    if url and not url.startswith("http") and e.get("id") and "youtube" in (e.get("ie_key") or "").lower():
        url = f"https://www.youtube.com/watch?v={e['id']}"
    return {
        "id": e.get("id"),
        "url": url,
        "title": e.get("title") or e.get("id") or "",
        "duration": e.get("duration"),
        "duration_text": hms(e["duration"]) if e.get("duration") else "",
        "uploader": e.get("channel") or e.get("uploader") or "",
        "thumbnail": thumb_for(e),
    }


def fetch_info(url):
    url = clean_url(url)
    now = time.time()
    with _info_lock:
        hit = _info_cache.get(url)
        if hit and now - hit[0] < INFO_TTL:
            return hit[1]
    cmd = ytdlp_cmd() + ["-J", "--flat-playlist", "--no-warnings", "--skip-download", "--", url]
    p = run_capture(cmd)
    if p.returncode != 0:
        raise ApiError(last_error_line(p.stderr or p.stdout), 422)
    try:
        info = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise ApiError("yt-dlp رجّع رد مش مفهوم", 502)

    if info.get("_type") == "playlist":
        out = {
            "type": "playlist",
            "title": info.get("title") or "Playlist",
            "url": url,
            "entries": [entry_summary(e) for e in (info.get("entries") or []) if e],
        }
    else:
        heights = sorted({f["height"] for f in (info.get("formats") or [])
                          if f.get("height") and f.get("vcodec") not in (None, "none")})
        langs = sorted(set((info.get("subtitles") or {}).keys()) | set((info.get("automatic_captions") or {}).keys()))
        fmts = info.get("formats") or []
        dur = info.get("duration") or 0

        def fsize(f):
            if f.get("filesize") or f.get("filesize_approx"):
                return f.get("filesize") or f.get("filesize_approx")
            return f["tbr"] * 1000 / 8 * dur if f.get("tbr") and dur else 0

        audio_sz = max([fsize(f) for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")] or [0])
        sizes = {}
        for h in heights:
            c = [fsize(f) for f in fmts if f.get("height") == h and f.get("vcodec") not in (None, "none")]
            c = [x for x in c if x]
            if c:
                sizes[str(h)] = int(sum(c) / len(c) + audio_sz)
        out = {
            "type": "video",
            "id": info.get("id"),
            "url": info.get("webpage_url") or url,
            "title": info.get("title") or "",
            "duration": info.get("duration") or 0,
            "duration_text": hms(info.get("duration")),
            "uploader": info.get("channel") or info.get("uploader") or "",
            "thumbnail": thumb_for(info),
            "is_live": bool(info.get("is_live")),
            "heights": heights,
            "sizes": sizes,
            "subtitle_langs": langs,
            "meta": discover_meta(info),
        }
    with _info_lock:
        _info_cache[url] = (now, out)
    return out


def search(query, count=12):
    query = (query or "").strip()
    if not query:
        raise ApiError("اكتب كلمة للبحث")
    cmd = ytdlp_cmd() + ["-J", "--flat-playlist", "--no-warnings", "--", f"ytsearch{count}:{query}"]
    p = run_capture(cmd)
    if p.returncode != 0:
        raise ApiError(last_error_line(p.stderr or p.stdout), 422)
    info = json.loads(p.stdout)
    return {"query": query, "entries": [entry_summary(e) for e in (info.get("entries") or []) if e]}


def suggest(query):
    query = (query or "").strip()
    if not query:
        return []
    url = "https://suggestqueries.google.com/complete/search?" + urlencode({"client": "firefox", "ds": "yt", "q": query})
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=4) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        return [s for s in data[1]][:8]
    except Exception:
        return []

# --------------------------------------------------------------------------- jobs

jobs = {}
jobs_lock = threading.Lock()
slots = threading.Semaphore(MAX_PARALLEL_JOBS)


def safe_name(s, fallback="download"):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", s or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s[:150] or fallback)


def parse_time(v, name):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.fullmatch(r"(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)", str(v).strip())
    if not m:
        raise ApiError(f"وقت غير صالح ({name}) — استخدم hh:mm:ss")
    a, b, c = m.groups()
    parts = [x for x in (a, b) if x is not None]
    h, mi = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (0, int(parts[0])) if len(parts) == 1 else (0, 0)
    return h * 3600 + mi * 60 + float(c)


class Job:
    def __init__(self, params):
        self.id = uuid.uuid4().hex[:12]
        self.params = params
        self.status = "queued"          # queued|running|done|error
        self.stage = "جارٍ الاستعداد…"
        self.progress = 0.0
        self.error = None
        self.filename = None
        self.path = None
        self.proc = None
        self.created = time.time()

    def public(self):
        return {"id": self.id, "status": self.status, "stage": self.stage,
                "progress": round(self.progress, 1), "error": self.error, "filename": self.filename}

    def set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def validate_params(d):
    url = clean_url(d.get("url"))
    fmt = d.get("format", "mp3")
    if fmt not in ("mp3", "mp4", "wav", "gif"):
        raise ApiError("صيغة غير مدعومة")
    try:
        q = int(d.get("quality") or 0)
    except (TypeError, ValueError):
        raise ApiError("جودة غير صالحة")
    if fmt == "mp3" and q not in AUDIO_QUALITIES["mp3"]:
        q = 128
    if fmt == "wav" and q not in WAV_RATES:
        q = 44100
    if fmt == "gif" and q not in GIF_WIDTHS:
        q = 480
    if fmt == "mp4" and q not in MP4_HEIGHTS:
        q = 720
    sub = bool(d.get("subtitles")) and fmt == "mp4"
    lang = str(d.get("subtitle_language") or "en")
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", lang):
        raise ApiError("لغة ترجمة غير صالحة")
    return {
        "url": url, "format": fmt, "quality": q,
        "start": parse_time(d.get("start"), "البداية"),
        "end": parse_time(d.get("end"), "النهاية"),
        "title": str(d.get("title") or "").strip()[:200],
        "artist": str(d.get("artist") or "").strip()[:200],
        "remove_silence": bool(d.get("remove_silence")),
        "normalize": bool(d.get("normalize")),
        "discover_metadata": bool(d.get("discover_metadata")),
        "h264": bool(d.get("h264")),
        "subtitles": sub, "subtitle_language": lang,
    }


def start_job(raw):
    params = validate_params(raw)
    info = fetch_info(params["url"])
    if info["type"] != "video":
        raise ApiError("ده رابط قائمة تشغيل — اختار فيديو واحد منها")
    if info["is_live"]:
        raise ApiError("البث المباشر مش مدعوم حالياً")
    dur = info["duration"] or 0
    s, e = params["start"], params["end"]
    if dur:
        if e is not None and e >= dur - 0.5:
            e = None
        if s is not None and s <= 0.5:
            s = None
    if s is not None and e is not None and e <= s:
        raise ApiError("وقت النهاية لازم يكون بعد البداية")
    if s is not None and dur and s >= dur:
        raise ApiError("وقت البداية بعد نهاية الفيديو")
    params["start"], params["end"] = s, e
    params["duration"] = (e if e is not None else dur) - (s or 0) if dur else None
    if params["format"] == "gif" and (params["duration"] is None or params["duration"] > MAX_GIF_SECONDS):
        raise ApiError(f"GIF أقصاه {MAX_GIF_SECONDS} ثانية — اختار مقطع أقصر بالسلايدر")
    params["info"] = info
    job = Job(params)
    with jobs_lock:
        jobs[job.id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return job


def stream_lines(job, cmd, on_line, cwd=None):
    job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                bufsize=1, cwd=cwd, errors="replace")
    tail = []
    for line in job.proc.stdout:
        line = line.rstrip("\n")
        tail.append(line)
        del tail[:-40]
        on_line(line)
    rc = job.proc.wait()
    return rc, "\n".join(tail)


def run_job(job):
    with slots:
        job.set(status="running")
        work = TMP / job.id
        try:
            work.mkdir(parents=True, exist_ok=True)
            out = convert(job, work)
            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            final = unique_path(DOWNLOADS / out.name)
            shutil.move(str(out), final)
            job.set(status="done", progress=100.0, stage="تم", filename=final.name, path=final)
        except ApiError as e:
            job.set(status="error", error=e.message)
        except Exception as e:  # noqa: BLE001
            job.set(status="error", error=f"خطأ غير متوقع: {e}")
        finally:
            shutil.rmtree(work, ignore_errors=True)


def unique_path(p):
    if not p.exists():
        return p
    for i in range(1, 1000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    return p.with_name(f"{p.stem}-{uuid.uuid4().hex[:6]}{p.suffix}")


def section_arg(p):
    s, e = p["start"], p["end"]
    if s is None and e is None:
        return []
    return ["--download-sections", f"*{s or 0}-{e if e is not None else 'inf'}", "--force-keyframes-at-cuts"]


def audio_filters(p):
    f = []
    # cutting silence shortens the audio only, which would desync a video — audio formats only
    if p["remove_silence"] and p["format"] in ("mp3", "wav"):
        f.append("silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:"
                 "stop_periods=-1:stop_duration=1:stop_threshold=-50dB")
    if p["normalize"]:
        f.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return ",".join(f)


def video_format_selector(p):
    h = p["quality"]
    if p["h264"]:
        return (f"bv*[vcodec^=avc1][height<={h}]+ba[ext=m4a]/bv*[height<={h}]+ba/"
                f"b[height<={h}]/b")
    return f"bv*[height<={h}]+ba/b[height<={h}]/b"


def convert(job, work):
    p = job.params
    info = p["info"]
    fmt = p["format"]
    meta = info["meta"] if p["discover_metadata"] else {"title": "", "artist": ""}
    title = p["title"] or meta["title"]
    artist = p["artist"] or meta["artist"]
    base_name = safe_name(f"{artist} - {title}" if artist and title else (title or info["title"]))

    # ---- 1. download with yt-dlp
    dl = ytdlp_cmd() + [
        "--no-playlist", "--no-warnings", "--newline", "--no-part", "--no-mtime",
        "--progress-template", "download:PROG|%(progress._percent_str)s",
        "-o", str(work / "src.%(ext)s"),
    ]
    want_thumb = fmt == "mp3" and p["discover_metadata"]
    if fmt in ("mp3", "wav"):
        dl += ["-f", "ba/b"]
    elif fmt == "gif":
        dl += ["-f", "bv*[height<=480]/b[height<=480]/b", "--merge-output-format", "mp4"]
    else:
        dl += ["-f", video_format_selector(p), "--merge-output-format", "mp4"]
    if p["subtitles"]:
        dl += ["--write-subs", "--write-auto-subs", "--sub-langs", p["subtitle_language"],
               "--convert-subs", "srt"]
    if want_thumb:
        dl += ["--write-thumbnail", "--convert-thumbnails", "jpg"]
    dl += section_arg(p)
    dl += ["--", p["url"]]

    def on_dl(line):
        m = re.match(r"PROG\|\s*([\d.]+)%", line)
        if m:
            pct = float(m.group(1))
            job.set(stage="جارٍ التنزيل…", progress=min(pct, 100) * 0.8)

    job.set(stage="جارٍ التنزيل…", progress=1)
    rc, tail = stream_lines(job, dl, on_dl)
    if rc != 0:
        raise ApiError(last_error_line(tail))

    srcs = [f for f in work.iterdir() if f.stem == "src" and f.suffix not in (".jpg", ".srt", ".vtt", ".webp", ".png")]
    if not srcs:
        raise ApiError("التنزيل خلص بس مفيش ملف ناتج")
    src = srcs[0]
    cover = next(iter(sorted(work.glob("src*.jpg"))), None) if want_thumb else None
    srt = next(iter(sorted(work.glob("src*.srt"))), None) if p["subtitles"] else None

    # ---- 2. convert with ffmpeg
    ff = [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    tags = []
    if title:
        tags += ["-metadata", f"title={title}"]
    if artist:
        tags += ["-metadata", f"artist={artist}"]
    af = audio_filters(p)
    out = work / f"out.{fmt}"
    expected = p["duration"]

    if fmt == "mp3":
        cmd = ff + ["-i", str(src)]
        if cover:
            cmd += ["-i", str(cover)]
        cmd += ["-map", "0:a:0"]
        if cover:
            cmd += ["-map", "1:v:0", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
                    "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
        if af:
            cmd += ["-af", af]
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{p['quality']}k", "-id3v2_version", "3"] + tags + [str(out)]
    elif fmt == "wav":
        cmd = ff + ["-i", str(src), "-vn"]
        if af:
            cmd += ["-af", af]
        cmd += ["-c:a", "pcm_s16le", "-ar", str(p["quality"])] + tags + [str(out)]
    elif fmt == "gif":
        w = p["quality"]
        vf = f"fps=10,scale={w}:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
        cmd = ff + ["-i", str(src), "-an", "-vf", vf, "-loop", "0", str(out)]
    else:  # mp4
        cmd = ff + ["-i", str(src)]
        if srt:
            cmd += ["-i", str(srt)]
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        if srt:
            cmd += ["-map", "1:0"]
        vcodec_is_h264 = probe_codec(src, "v") == "h264"
        if p["h264"] and not vcodec_is_h264:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        else:
            cmd += ["-c:v", "copy"]
        if af:
            cmd += ["-af", af, "-c:a", "aac", "-b:a", "192k"]
        elif p["h264"] and probe_codec(src, "a") not in ("aac", None):
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-c:a", "copy"]
        if srt:
            cmd += ["-c:s", "mov_text", "-metadata:s:s:0", f"language={p['subtitle_language'][:3]}"]
        cmd += ["-movflags", "+faststart"] + tags + [str(out)]

    def on_ff(line):
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                t = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                return
            if expected:
                job.set(stage="جارٍ التحويل…", progress=80 + min(t / expected, 1) * 19)

    job.set(stage="جارٍ التحويل…", progress=80)
    rc, tail = stream_lines(job, cmd, on_ff)
    if rc != 0 or not out.exists():
        raise ApiError("فشل التحويل: " + last_error_line(tail))
    final = work / f"{base_name}.{fmt}"
    out.rename(final)
    return final


def probe_codec(path, kind):
    fp = ffprobe_bin()
    if not fp:
        return None
    r = subprocess.run([fp, "-v", "error", "-select_streams", f"{kind}:0", "-show_entries", "stream=codec_name",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return r.stdout.strip() or None

# --------------------------------------------------------------------------- web

def render(page, **ctx):
    layout = (TEMPLATES / "layout.html").read_text(encoding="utf-8")
    body = (TEMPLATES / f"{page}.html").read_text(encoding="utf-8")
    html = layout.replace("{{content}}", body)
    for k, v in ctx.items():
        html = html.replace("{{" + k + "}}", v)
    return html.encode("utf-8")


PAGES = {
    "/": ("index", "Yout"),
    "/video/": ("video", "أداة تحويل تنسيق الفيديو"),
    "/s/": ("search", "بحث"),
    "/api/": ("api", "API"),
    "/downloads/": ("downloads", "التحميلات"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "YoutLocal/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("YOUT_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers
    def send(self, code, body=b"", ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, code=200):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                  {"Cache-Control": "no-store"})

    def redirect(self, to):
        self.send(302, b"", headers={"Location": to})

    def host_ok(self):
        # blocks DNS-rebinding and cross-site requests against this local service
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        allowed = {"localhost", "127.0.0.1", "[::1]", "::1"} | set(self.server.extra_hosts)
        if host not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return False
        return True

    def file_response(self, path, download_name=None):
        size = path.stat().st_size
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        if download_name:
            self.send_header("Content-Disposition",
                             f"attachment; filename=\"download{path.suffix}\"; filename*=UTF-8''{quote(download_name)}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return False
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, 1024 * 256)
        return True   # whole file written (an interrupted download raises before this)

    # -- routing
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self.route_get()
        except ApiError as e:
            self.json({"error": e.message}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            self.json({"error": f"server error: {e}"}, 500)

    def do_POST(self):
        try:
            if not self.host_ok() or self.headers.get("X-Requested-With") != "yout-local":
                raise ApiError("forbidden", 403)
            n = int(self.headers.get("Content-Length") or 0)
            if n > 100_000:
                raise ApiError("too large", 413)
            data = json.loads(self.rfile.read(n) or b"{}")
            if urlparse(self.path).path == "/api/jobs":
                self.json(start_job(data).public(), 202)
            else:
                raise ApiError("not found", 404)
        except ApiError as e:
            self.json({"error": e.message}, e.status)
        except json.JSONDecodeError:
            self.json({"error": "bad json"}, 400)
        except Exception as e:  # noqa: BLE001
            self.json({"error": f"server error: {e}"}, 500)

    def do_DELETE(self):
        try:
            if not self.host_ok() or self.headers.get("X-Requested-With") != "yout-local":
                raise ApiError("forbidden", 403)
            u = urlparse(self.path)
            m = re.fullmatch(r"/api/downloads/(.+)", u.path)
            if not m:
                raise ApiError("not found", 404)
            p = safe_download_path(unquote(m.group(1)))
            p.unlink()
            self.json({"ok": True})
        except ApiError as e:
            self.json({"error": e.message}, e.status)

    def route_get(self):
        if not self.host_ok():
            raise ApiError("forbidden", 403)
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        q = lambda k: (qs.get(k) or [""])[0]

        # shortcut like the original site's "remove 'ube' from the URL" trick
        if path == "/watch" and q("v"):
            return self.redirect("/video/?" + urlencode({"url": f"https://www.youtube.com/watch?v={q('v')}"}))

        if path == "/s/" and re.match(r"^https?://", q("q").strip()):
            return self.redirect("/video/?" + urlencode({"url": q("q").strip()}))

        if path in PAGES:
            page, title = PAGES[path]
            return self.send(200, render(page, title=title))
        if path in ("/video", "/s", "/api", "/downloads"):
            return self.redirect(path + "/" + ("?" + u.query if u.query else ""))

        if path.startswith("/static/"):
            f = (STATIC / unquote(path[len("/static/"):])).resolve()
            if STATIC not in f.parents or not f.is_file():
                raise ApiError("not found", 404)
            return self.file_response(f)
        if path == "/favicon.ico":
            return self.file_response(STATIC / "img" / "logo.png")

        if path == "/api/info":
            return self.json(fetch_info(q("url")))
        if path == "/api/search":
            return self.json(search(q("q")))
        if path == "/api/suggest":
            return self.json(suggest(q("q")))
        if path == "/api/downloads":
            return self.json(list_downloads())
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})(/file)?", path)
        if m:
            job = jobs.get(m.group(1))
            if not job:
                raise ApiError("job not found", 404)
            if not m.group(2):
                return self.json(job.public())
            if job.status != "done" or not job.path or not Path(job.path).exists():
                raise ApiError("الملف لسه مش جاهز", 409)
            sent = self.file_response(Path(job.path), job.filename)
            # one copy only: once the browser has the whole file, drop the server-side copy
            if sent and self.command == "GET" and not self.server.keep_copy:
                try:
                    Path(job.path).unlink()
                    job.path = None
                except OSError:
                    pass
            return sent
        m = re.fullmatch(r"/api/downloads/(.+)", path)
        if m:
            p = safe_download_path(unquote(m.group(1)))
            return self.file_response(p, p.name)
        raise ApiError("not found", 404)


def safe_download_path(name):
    p = (DOWNLOADS / name).resolve()
    if p.parent != DOWNLOADS or not p.is_file() or p.name.startswith("."):
        raise ApiError("file not found", 404)
    return p


def list_downloads():
    items = []
    if DOWNLOADS.exists():
        for f in DOWNLOADS.iterdir():
            if f.is_file() and not f.name.startswith("."):
                st = f.stat()
                items.append({"name": f.name, "size": st.st_size, "mtime": int(st.st_mtime)})
    items.sort(key=lambda x: -x["mtime"])
    return {"dir": str(DOWNLOADS), "items": items}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    extra_hosts = ()
    keep_copy = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 only if you really want LAN access")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--allow-host", action="append", default=[], help="extra Host header names to accept")
    ap.add_argument("--keep-copy", action="store_true",
                    help="also keep a copy in the downloads/ folder (default: only the browser download is kept)")
    a = ap.parse_args()
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(TMP, ignore_errors=True)
    srv = Server((a.host, a.port), Handler)
    srv.keep_copy = a.keep_copy
    srv.extra_hosts = tuple(h.lower() for h in a.allow_host)
    print(f"Yout local → http://{a.host if a.host != '0.0.0.0' else 'localhost'}:{a.port}   (files: {DOWNLOADS})")
    try:
        ytdlp_cmd(); ffmpeg_bin()
    except RuntimeError as e:
        print("WARNING:", e)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
