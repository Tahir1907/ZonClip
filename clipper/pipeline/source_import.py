"""Safe, local import of public Twitch VOD and YouTube video URLs.

Only the two explicit platform host families are accepted. This does not use browser
cookies, login sessions, DRM workarounds, or private-content access.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from urllib.parse import urlparse


VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".ts", ".flv"}
HOSTS = {
    "twitch": {"twitch.tv", "www.twitch.tv"},
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
}


def _validate_url(url: str, platform: str) -> str:
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("That link is too long or invalid.")
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise ValueError("Use a normal HTTPS video link.")
    if host not in HOSTS.get(platform, set()):
        raise ValueError(f"That is not a supported {platform.title()} link.")
    if platform == "twitch" and not re.match(r"^/videos/\d+", parsed.path):
        raise ValueError("Paste a completed Twitch VOD link in the form twitch.tv/videos/…")
    if platform == "youtube":
        valid = (host == "youtu.be" and bool(parsed.path.strip("/"))) or (
            host != "youtu.be" and (
                parsed.path == "/watch" and bool(parsed.parse_qs if False else True)
                or re.match(r"^/(shorts|live|embed|v)/[^/]+", parsed.path)
            )
        )
        if not valid:
            raise ValueError("Paste a YouTube video link, not a channel or playlist page.")
    return url.strip()


def download_source(url: str, platform: str, library_root: str, progress_cb=None):
    """Download one permitted public VOD/video to the local library and return (path, title)."""
    platform = (platform or "").lower()
    if platform not in HOSTS:
        raise ValueError("Choose Twitch or YouTube.")
    url = _validate_url(url, platform)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("The link importer is not installed yet. Run setup.ps1 again to install yt-dlp.") from exc

    os.makedirs(library_root, exist_ok=True)
    temp_dir = tempfile.mkdtemp(prefix=".zonclip-import-", dir=library_root)
    title = "Imported video"
    def progress(data):
        if not progress_cb:
            return
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            frac = min(0.95, downloaded / total) if total else 0.05
            progress_cb("Downloading video to your PC…", frac)
        elif status == "finished":
            progress_cb("Finishing video file…", 0.97)

    options = {
        "outtmpl": os.path.join(temp_dir, "%(title).90B-%(id)s.%(ext)s"),
        "noplaylist": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "max_filesize": 10 * 1024 * 1024 * 1024,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
        "progress_hooks": [progress],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            if progress_cb:
                progress_cb("Checking video link…", 0.01)
            info = ydl.extract_info(url, download=False)
            if not info or info.get("_type") in ("playlist", "multi_video"):
                raise ValueError("Paste one video or VOD link, not a playlist.")
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                raise ValueError("Live streams and upcoming streams are not supported by this VOD importer yet. Use a completed VOD.")
            title = info.get("title") or title
            if progress_cb:
                progress_cb("Downloading video to your PC…", 0.03)
            ydl.download([url])

        candidates = []
        for name in os.listdir(temp_dir):
            path = os.path.join(temp_dir, name)
            if os.path.isfile(path) and os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                candidates.append(path)
        if not candidates:
            raise RuntimeError("The source did not produce a supported video file. It may be private, unavailable, or restricted.")
        source = max(candidates, key=os.path.getsize)
        if os.path.getsize(source) <= 1_000_000:
            raise RuntimeError("The imported video is too small or incomplete.")
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", os.path.splitext(os.path.basename(source))[0]).strip(" .")
        stem = stem[:120] or "Imported video"
        dest = os.path.join(library_root, stem + os.path.splitext(source)[1].lower())
        n = 2
        while os.path.exists(dest):
            dest = os.path.join(library_root, f"{stem} ({n}){os.path.splitext(source)[1].lower()}")
            n += 1
        shutil.move(source, dest)
        if progress_cb:
            progress_cb("Import complete", 1.0)
        return dest, title
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
