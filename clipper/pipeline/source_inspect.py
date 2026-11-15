"""Metadata-only inspection for supported public video URLs.

This module never asks yt-dlp to download media. It is intentionally separate
from source_import.py, whose legacy function downloads complete source videos.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .source_import import _validate_url


def inspect_source_url(url: str, platform: str) -> dict:
    platform = (platform or "").strip().lower()
    if platform not in ("youtube", "twitch"):
        raise ValueError("Choose YouTube or Twitch.")
    safe_url = _validate_url(url, platform)
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
        "retries": 1,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(safe_url, download=False)
    if not info:
        raise RuntimeError("The platform did not return video metadata.")
    if info.get("_type") in ("playlist", "multi_video"):
        raise ValueError("Paste one individual video or VOD URL, not a playlist.")
    live_status = info.get("live_status") or ("is_live" if info.get("is_live") else "not_live")
    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    return {
        "platform": platform,
        "title": info.get("title") or "Untitled video",
        "creator": info.get("channel") or info.get("uploader") or info.get("creator") or "",
        "duration": info.get("duration"),
        "live_status": live_status,
        "is_live": bool(info.get("is_live")),
        "is_upcoming": live_status == "is_upcoming",
        "has_captions": bool(subtitles or automatic),
        "caption_languages": sorted(set(subtitles) | set(automatic)),
        "availability": info.get("availability") or "public_or_unclassified",
        "media_downloaded": False,
        "full_source_saved": False,
    }
