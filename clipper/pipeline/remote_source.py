"""Range-based YouTube media access for the CPU-first, no-full-video-download path.

The resolver inspects metadata and returns short-lived stream URLs. Audio analysis may
retrieve an audio-only track; video retrieval is restricted to selected short windows.
The complete source video is never saved by these helpers.
"""
from __future__ import annotations

import os
import subprocess
from urllib.parse import urlparse

from . import youtube_source


def _http_url(value: str) -> bool:
    return urlparse(str(value or "")).scheme in ("https", "http")


def _headers_arg(headers: dict) -> str:
    """Convert yt-dlp response headers to FFmpeg's CRLF header block."""
    safe = []
    for key, value in (headers or {}).items():
        k, v = str(key), str(value)
        if "\r" in k or "\n" in k or ":" in k or "\r" in v or "\n" in v:
            continue
        safe.append(f"{k}: {v}")
    return "\r\n".join(safe) + ("\r\n" if safe else "")


def _media_tracks(info: dict) -> dict:
    """Normalize either one progressive stream or separate adaptive A/V streams."""
    requested = info.get("requested_formats") or []
    video = next((f for f in requested if f.get("vcodec") not in (None, "none") and f.get("url")), None)
    audio = next((f for f in requested if f.get("acodec") not in (None, "none") and f.get("url")), None)

    # yt-dlp returns requested_formats for a video+audio adaptive selection.
    if video and audio:
        return {
            "video_url": video["url"],
            "audio_url": audio["url"],
            "video_headers": video.get("http_headers") or info.get("http_headers") or {},
            "audio_headers": audio.get("http_headers") or info.get("http_headers") or {},
            "protocol": video.get("protocol") or info.get("protocol"),
            "format_id": info.get("format_id") or f'{video.get("format_id")}+{audio.get("format_id")}',
        }

    # Progressive stream: one URL contains both audio and video.
    media_url = info.get("url")
    if media_url and info.get("vcodec") not in (None, "none") and info.get("acodec") not in (None, "none"):
        headers = info.get("http_headers") or {}
        return {
            "media_url": media_url,
            "http_headers": headers,
            "protocol": info.get("protocol"),
            "format_id": info.get("format_id"),
        }

    # Some extractors return a single selected format in requested_formats.
    progressive = next((f for f in requested if f.get("vcodec") not in (None, "none")
                        and f.get("acodec") not in (None, "none") and f.get("url")), None)
    if progressive:
        return {
            "media_url": progressive["url"],
            "http_headers": progressive.get("http_headers") or info.get("http_headers") or {},
            "protocol": progressive.get("protocol") or info.get("protocol"),
            "format_id": progressive.get("format_id") or info.get("format_id"),
        }
    raise RuntimeError("YouTube did not provide usable audio/video streams. No full-video download fallback is allowed.")


def resolve_youtube_media(url: str) -> dict:
    """Resolve one public completed YouTube video without downloading its media."""
    descriptor = youtube_source.parse_youtube_url(url)
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("Install the project requirements before processing YouTube links.") from exc

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        # Prefer a single progressive stream, but support adaptive DASH A/V when needed.
        "format": (
            "best[protocol^=https][vcodec!=none][acodec!=none]/"
            "best[protocol^=http][vcodec!=none][acodec!=none]/"
            "bestvideo[protocol^=https][vcodec!=none]+bestaudio[protocol^=https][acodec!=none]/"
            "bestvideo+bestaudio"
        ),
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(descriptor["url"], download=False)

    if not info:
        raise RuntimeError("YouTube did not return media information.")
    if info.get("_type") in ("playlist", "multi_video"):
        raise ValueError("Paste one individual YouTube video, not a playlist.")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise RuntimeError("This is a live or upcoming stream. Completed-video processing is the first supported phase.")

    tracks = _media_tracks(info)
    urls = [tracks.get("media_url"), tracks.get("video_url"), tracks.get("audio_url")]
    if not all(_http_url(value) for value in urls if value):
        raise RuntimeError("The media provider returned an unsupported stream URL.")
    if not tracks.get("media_url") and not (tracks.get("video_url") and tracks.get("audio_url")):
        raise RuntimeError("The source did not provide both audio and video tracks.")

    return {
        **descriptor,
        "title": info.get("title") or "YouTube video",
        "duration": info.get("duration"),
        **tracks,                         # signed URLs are short-lived; never persist them
        "width": info.get("width"),
        "height": info.get("height"),
        "video_downloaded": False,
        "full_source_saved": False,
    }


def _audio_input(source: dict) -> tuple[list[str], str]:
    """Return FFmpeg input args and the selected audio URL."""
    url = source.get("audio_url") or source.get("media_url")
    headers = source.get("audio_headers") or source.get("http_headers") or {}
    if not _http_url(url):
        raise ValueError("Resolve a valid YouTube audio stream first.")
    args = []
    block = _headers_arg(headers)
    if block:
        args += ["-headers", block]
    args += ["-i", url]
    return args, url


def extract_audio_track(source: dict, output_wav: str, ffmpeg: str = "ffmpeg") -> str:
    """Retrieve the complete audio track only for local transcription (never the source video)."""
    args, _url = _audio_input(source)
    os.makedirs(os.path.dirname(os.path.abspath(output_wav)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", *args,
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-threads", "2", output_wav]
    # Long VODs can take time to transfer; cap at 12 hours rather than hanging forever.
    timeout = max(600, min(43200, int((source.get("duration") or 3600) * 3 + 300)))
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)
    return output_wav


def extract_audio_window(source: dict, start_s: float, duration_s: float, output_wav: str,
                         ffmpeg: str = "ffmpeg") -> str:
    """Fetch/decode one short audio window into a temporary mono 16 kHz WAV."""
    start_s, duration_s = float(start_s), float(duration_s)
    if start_s < 0 or not 0.5 <= duration_s <= 180:
        raise ValueError("Audio window must be 0.5–180 seconds.")
    args, _url = _audio_input(source)
    os.makedirs(os.path.dirname(os.path.abspath(output_wav)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start_s:.3f}",
           *args, "-t", f"{duration_s:.3f}", "-vn", "-ac", "1", "-ar", "16000",
           "-c:a", "pcm_s16le", "-threads", "2", output_wav]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=240)
    return output_wav


def extract_video_window(source: dict, start_s: float, duration_s: float, output_path: str,
                         ffmpeg: str = "ffmpeg") -> str:
    """Fetch only one selected short video range; never save the complete source video."""
    start_s, duration_s = float(start_s), float(duration_s)
    if start_s < 0 or not 1 <= duration_s <= 180:
        raise ValueError("Video window must be 1–180 seconds.")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start_s:.3f}"]

    video_url = source.get("video_url")
    audio_url = source.get("audio_url")
    if video_url:
        if not _http_url(video_url) or not _http_url(audio_url):
            raise ValueError("Resolve valid separate YouTube audio/video streams first.")
        video_headers = _headers_arg(source.get("video_headers") or {})
        if video_headers:
            cmd += ["-headers", video_headers]
        cmd += ["-i", video_url, "-ss", f"{start_s:.3f}"]
        audio_headers = _headers_arg(source.get("audio_headers") or {})
        if audio_headers:
            cmd += ["-headers", audio_headers]
        cmd += ["-i", audio_url, "-t", f"{duration_s:.3f}", "-map", "0:v:0",
                "-map", "1:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", output_path]
    else:
        media_url = source.get("media_url", "")
        if not _http_url(media_url):
            raise ValueError("Resolve a valid YouTube media stream first.")
        headers = _headers_arg(source.get("http_headers") or {})
        if headers:
            cmd += ["-headers", headers]
        cmd += ["-i", media_url, "-t", f"{duration_s:.3f}", "-map", "0:v:0",
                "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", output_path]

    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
    return output_path
