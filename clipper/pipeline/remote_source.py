"""Range-based YouTube media access for the CPU-first, no-full-source-download path.

The resolver only inspects metadata and returns short-lived media URLs. The helpers
ask FFmpeg for a requested time window; they never save the complete source video.
A short temporary audio/video segment may be created for analysis/rendering.
"""
from __future__ import annotations

import os
import subprocess
from urllib.parse import urlparse

from . import youtube_source


def resolve_youtube_media(url: str) -> dict:
    """Resolve one public YouTube video to a progressive A/V URL without downloading it.

    Progressive formats are preferred because they contain audio and video in one
    HTTP resource and are simpler to range-seek on low-powered machines. If YouTube
    only exposes separate DASH tracks, fail clearly rather than silently downloading
    the whole source or attempting an expensive merge.
    """
    descriptor = youtube_source.parse_youtube_url(url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("Install the project requirements before importing YouTube links.") from exc

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "format": "best[protocol^=https][vcodec!=none][acodec!=none]/best[protocol^=http][vcodec!=none][acodec!=none]",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(descriptor["url"], download=False)

    if not info:
        raise RuntimeError("YouTube did not return media information.")
    if info.get("is_live") or info.get("live_status") == "is_live":
        raise RuntimeError("This link is a live stream. Rolling live capture is not enabled in this VOD range reader.")
    media_url = info.get("url")
    if not media_url:
        raise RuntimeError("YouTube did not provide a single progressive audio/video stream. No full-download fallback is allowed.")
    parsed = urlparse(media_url)
    if parsed.scheme not in ("https", "http"):
        raise RuntimeError("The media provider returned an unsupported stream URL.")

    return {
        **descriptor,
        "title": info.get("title") or "YouTube video",
        "duration": info.get("duration"),
        "media_url": media_url,             # short-lived; do not persist
        "http_headers": info.get("http_headers") or {},
        "width": info.get("width"),
        "height": info.get("height"),
        "format_id": info.get("format_id"),
        "protocol": info.get("protocol"),
        "video_downloaded": False,
        "full_source_saved": False,
    }


def _headers_arg(headers: dict) -> str:
    """Convert yt-dlp's response headers to FFmpeg's CRLF header block."""
    safe = []
    for key, value in (headers or {}).items():
        k, v = str(key), str(value)
        if "\r" in k or "\n" in k or ":" in k or "\r" in v or "\n" in v:
            continue
        safe.append(f"{k}: {v}")
    return "\r\n".join(safe) + ("\r\n" if safe else "")


def extract_audio_window(source: dict, start_s: float, duration_s: float, output_wav: str,
                         ffmpeg: str = "ffmpeg") -> str:
    """Fetch/decode only one requested audio window into a temporary mono 16 kHz WAV.

    This does not write a source video file. The caller should delete the WAV after
    scoring/transcription; keep windows short to bound disk and memory use.
    """
    start_s, duration_s = float(start_s), float(duration_s)
    if start_s < 0 or not 0.5 <= duration_s <= 180:
        raise ValueError("Audio window must be 0.5–180 seconds.")
    media_url = source.get("media_url", "")
    if urlparse(media_url).scheme not in ("https", "http"):
        raise ValueError("Resolve a valid YouTube media stream first.")
    os.makedirs(os.path.dirname(os.path.abspath(output_wav)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start_s:.3f}"]
    headers = _headers_arg(source.get("http_headers") or {})
    if headers:
        cmd += ["-headers", headers]
    cmd += ["-i", media_url, "-t", f"{duration_s:.3f}", "-vn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-threads", "2",
            output_wav]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=240)
    return output_wav


def extract_video_window(source: dict, start_s: float, duration_s: float, output_path: str,
                         ffmpeg: str = "ffmpeg") -> str:
    """Fetch only a selected short video range to a temporary MP4 for the renderer.

    FFmpeg input seeking relies on the host's HTTP range support. The caller must
    remove this temporary segment after rendering; the complete source is never saved.
    """
    start_s, duration_s = float(start_s), float(duration_s)
    if start_s < 0 or not 1 <= duration_s <= 180:
        raise ValueError("Video window must be 1–180 seconds.")
    media_url = source.get("media_url", "")
    if urlparse(media_url).scheme not in ("https", "http"):
        raise ValueError("Resolve a valid YouTube media stream first.")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", f"{start_s:.3f}"]
    headers = _headers_arg(source.get("http_headers") or {})
    if headers:
        cmd += ["-headers", headers]
    cmd += ["-i", media_url, "-t", f"{duration_s:.3f}", "-map", "0:v:0",
            "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero",
            output_path]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
    return output_path
