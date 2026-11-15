"""YouTube URL and metadata inspection for the hybrid source pipeline.

This module deliberately does not download video media. It normalizes common
YouTube URL shapes and asks yt-dlp for public metadata/caption availability.
Media retrieval and rolling live capture are separate pipeline stages.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
}
SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_youtube_url(url: str) -> dict:
    """Return a normalized single-video descriptor or raise ValueError.

    Accepts watch, youtu.be, shorts, live, embed, /v/ and playlist URLs
    that include a concrete video id. Playlist-only pages are rejected.
    """
    if not isinstance(url, str) or not url.strip() or len(url) > 4096:
        raise ValueError("Paste a valid YouTube video link.")
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
        raise ValueError("Use a normal YouTube link.")
    if parsed.port or host not in YOUTUBE_HOSTS | SHORT_HOSTS:
        raise ValueError("That link is not from a supported YouTube domain.")

    query = parse_qs(parsed.query)
    video_id = ""
    path_parts = [part for part in parsed.path.split("/") if part]
    kind = "video"

    if host in SHORT_HOSTS:
        video_id = path_parts[0] if path_parts else ""
        kind = "short_link"
    elif parsed.path == "/watch":
        video_id = (query.get("v") or [""])[0]
        kind = "watch"
    elif path_parts and path_parts[0] in {"shorts", "live", "embed", "v"}:
        if len(path_parts) >= 2:
            video_id = path_parts[1]
        kind = path_parts[0]
    elif parsed.path == "/playlist":
        video_id = (query.get("v") or [""])[0]
        kind = "playlist_video"
    else:
        raise ValueError("Unsupported YouTube URL format. Paste a video, Shorts, live, or embed link.")

    if not VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError("This link does not contain a valid individual YouTube video ID.")

    return {
        "video_id": video_id,
        "kind": kind,
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def inspect_youtube_url(url: str) -> dict:
    """Inspect a public YouTube item without downloading its video/audio."""
    descriptor = parse_youtube_url(url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp is required. Install the project requirements first.") from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(descriptor["url"], download=False)

    if not info:
        raise RuntimeError("YouTube did not return video information.")
    live_status = info.get("live_status") or ("is_live" if info.get("is_live") else "not_live")
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    return {
        **descriptor,
        "title": info.get("title") or "Untitled YouTube video",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": info.get("duration"),
        "live_status": live_status,
        "is_live": bool(info.get("is_live")),
        "is_upcoming": live_status == "is_upcoming",
        "has_manual_captions": bool(manual),
        "has_automatic_captions": bool(automatic),
        "caption_languages": sorted(set(manual) | set(automatic)),
        "availability": info.get("availability") or "public_or_unclassified",
        "video_downloaded": False,
    }
