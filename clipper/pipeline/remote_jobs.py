"""Completed YouTube VOD -> local highlights -> selected-range editor proxy.

Only the audio track is retrieved for analysis. After local detection, the worker fetches
short video windows around the selected moments and concatenates those windows into a
small local preview source for the existing ClipForge editor. It never downloads the
complete source video and has no full-video fallback.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid

from . import config, remote_source, transcribe, util
from .util import log


def _safe_title(value: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "", value or "").strip(" .-_")
    name = re.sub(r"\s+", " ", name)[:72].strip(" .-_")
    return name or fallback


def _new_output_dir(library_root: str, title: str, video_id: str) -> str:
    os.makedirs(library_root, exist_ok=True)
    base = os.path.join(library_root, f"{_safe_title(title, video_id)} - clips")
    path, suffix = base, 2
    while os.path.exists(path):
        path = f"{base} ({suffix})"
        suffix += 1
    os.makedirs(path)
    return path


def _select_candidates(candidates: list, count: int, min_gap_s: float = 20.0) -> list:
    """Keep top-scoring candidates while avoiding overlapping or nearly adjacent moments."""
    chosen = []
    for candidate in candidates:
        if any(candidate.start < item.end + min_gap_s and candidate.end > item.start - min_gap_s
               for item in chosen):
            continue
        chosen.append(candidate)
        if len(chosen) >= count:
            break
    return chosen


def _concat_escape(path: str) -> str:
    """Escape a path for the FFmpeg concat demuxer file-list syntax."""
    return os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")


def _run_ffmpeg(args: list[str], timeout: int = 900) -> None:
    try:
        subprocess.run([util.ffmpeg(), *args], check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()[-700:]
        raise RuntimeError(f"FFmpeg could not prepare the selected media windows. {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Media preparation timed out. Try a shorter video or check your connection.") from exc


def make_youtube_clips(cfg: config.Config, url: str, library_root: str, count: int | None = None,
                       progress_cb=None) -> tuple[str, dict]:
    """Create an editable ClipForge project from one completed public YouTube video.

    Returns (output_directory, project). The complete source video is never stored.
    The source's audio-only track is used for local transcription; selected short video
    windows are combined into source-preview.mp4 so the existing editor can trim/re-render.
    """
    def progress(phase: str, fraction: float | None = None):
        log(f"[youtube] {phase}")
        if progress_cb:
            progress_cb(phase, fraction)

    from . import branding, detect, project as proj, render

    count = max(1, min(30, int(count or cfg.clip_count)))
    source = remote_source.resolve_youtube_media(url)
    duration = float(source.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("YouTube did not report a usable video duration.")
    if duration > 12 * 60 * 60:
        raise RuntimeError("Videos longer than 12 hours are not supported in this first version.")

    output_dir = _new_output_dir(library_root, source["title"], source["video_id"])
    util.ensure_dirs(config.WORK_DIR)
    run_id = uuid.uuid4().hex[:8]
    proxy_path = os.path.join(output_dir, f"source-preview-{source['video_id']}-{run_id}.mp4")
    temp_dir = tempfile.mkdtemp(prefix=f"youtube_{source['video_id']}_", dir=config.WORK_DIR)
    analysis_stub = os.path.join(temp_dir, f"analysis_{source['video_id']}_{run_id}.mp4")
    analysis_wav = os.path.join(temp_dir, "analysis-audio.wav")
    analysis_transcript_path = None
    completed = False

    try:
        progress("Retrieving audio-only track for local analysis (no source video download)", 0.05)
        remote_source.extract_audio_track(source, analysis_wav)

        progress("Transcribing locally on your CPU", 0.15)
        analysis_transcript_path = transcribe.transcript_path(cfg, analysis_stub)
        transcript = transcribe.transcribe(cfg, analysis_wav, analysis_stub, force=True)
        if not transcript.get("words"):
            raise RuntimeError("No speech was detected. This first version needs a usable spoken-audio track.")

        progress("Scoring highlights locally", 0.55)
        candidates = detect.detect(cfg, transcript, analysis_wav)
        selected = _select_candidates(candidates, count)
        if not selected:
            raise RuntimeError("No suitable highlights were found in this video.")

        # Download only the selected time ranges, with a little context for editor trimming.
        pad_s = 8.0
        ranges = []
        for idx, candidate in enumerate(selected, 1):
            window_start = max(0.0, float(candidate.start) - pad_s)
            window_end = min(duration, float(candidate.end) + pad_s)
            window_duration = window_end - window_start
            if not 1.0 <= window_duration <= 180.0:
                raise RuntimeError("A detected highlight exceeded the selected-range limit (180 seconds).")
            ranges.append({
                "candidate": candidate,
                "source_start": window_start,
                "source_end": window_end,
                "requested_duration": window_duration,
                "file": os.path.join(temp_dir, f"selected_{idx:02d}.mp4"),
            })

        # Concat order is chronological, while project rank remains the detector's score order.
        chronological = sorted(ranges, key=lambda item: item["source_start"])
        progress("Fetching only selected video sections", 0.65)
        offset = 0.0
        for idx, item in enumerate(chronological, 1):
            remote_source.extract_video_window(
                source, item["source_start"], item["requested_duration"], item["file"]
            )
            actual_duration = util.probe_duration(item["file"])
            if actual_duration <= 1.0:
                raise RuntimeError("A selected YouTube video section could not be decoded.")
            item["proxy_offset"] = offset
            item["actual_duration"] = actual_duration
            item["editor_start"] = offset + min(
                max(0.0, item["candidate"].start - item["source_start"]),
                max(0.0, actual_duration - (item["candidate"].end - item["candidate"].start)),
            )
            item["editor_end"] = item["editor_start"] + (item["candidate"].end - item["candidate"].start)
            if item["editor_end"] > offset + actual_duration + 0.25:
                raise RuntimeError("The retrieved media section was shorter than its highlight.")
            offset += actual_duration
            progress(f"Retrieved selected section {idx} of {len(chronological)}",
                     0.65 + 0.12 * idx / len(chronological))

        progress("Building the compact editable preview", 0.79)
        concat_list = os.path.join(temp_dir, "segments.txt")
        with open(concat_list, "w", encoding="utf-8") as fh:
            for item in chronological:
                fh.write("file '" + _concat_escape(item["file"]) + "'\n")
        _run_ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                     "-i", concat_list, "-c", "copy", "-movflags", "+faststart", proxy_path], timeout=900)
        proxy_duration = util.probe_duration(proxy_path)
        if proxy_duration <= 1.0:
            raise RuntimeError("The selected sections could not be combined into an editor preview.")

        # Shift detected source timestamps into the compact proxy's timeline.
        proxy_transcript = {"language": transcript.get("language", cfg.language),
                            "duration": proxy_duration, "model": cfg.model, "device": "cpu",
                            "words": [], "segments": []}
        by_candidate = {id(item["candidate"]): item for item in chronological}
        for candidate in selected:
            item = by_candidate[id(candidate)]
            old_start, old_end = float(candidate.start), float(candidate.end)
            shift = item["editor_start"] - old_start
            candidate.start = round(item["editor_start"], 3)
            candidate.end = round(item["editor_end"], 3)
            candidate.peak = round(float(candidate.peak) + shift, 3)

        for item in chronological:
            src_a, src_b = item["source_start"], item["source_end"]
            shift = item["proxy_offset"] - src_a
            for word in transcript.get("words", []):
                if word["end"] > src_a and word["start"] < src_b:
                    proxy_transcript["words"].append({
                        "start": max(0.0, round(word["start"] + shift, 3)),
                        "end": round(min(src_b, word["end"]) + shift, 3),
                        "word": word["word"],
                    })
            for seg in transcript.get("segments", []):
                if seg["end"] > src_a and seg["start"] < src_b:
                    proxy_transcript["segments"].append({
                        "start": max(0.0, round(seg["start"] + shift, 3)),
                        "end": round(min(src_b, seg["end"]) + shift, 3),
                        "text": seg["text"],
                    })
        proxy_transcript["words"].sort(key=lambda item: item["start"])
        proxy_transcript["segments"].sort(key=lambda item: item["start"])

        # The editor's clip-level captioner reads this WAV; it contains only selected ranges.
        proxy_wav = transcribe.wav_path(proxy_path)
        _run_ffmpeg(["-y", "-hide_banner", "-loglevel", "error", "-i", proxy_path, "-vn",
                     "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", proxy_wav], timeout=900)
        util.write_json(transcribe.transcript_path(cfg, proxy_path), proxy_transcript)

        progress("Preparing editable clips", 0.84)
        project = proj.build_project(cfg, proxy_path, selected, ai=False)
        project["source"] = {
            "platform": "youtube",
            "video_id": source["video_id"],
            "url": source["url"],
            "title": source["title"],
            "duration": duration,
            "complete_source_video_saved": False,
            "audio_only_analysis": True,
            "selected_source_ranges": [
                {"start": round(item["source_start"], 2), "end": round(item["source_end"], 2)}
                for item in chronological
            ],
        }

        util.ensure_fonts()
        watermark = branding.render_watermark(cfg, force=True)
        from . import server as _server
        total = len(project["clips"])
        for idx, clip in enumerate(project["clips"], 1):
            progress(f"Rendering vertical clip {idx} of {total}", 0.85 + 0.13 * (idx - 1) / max(1, total))
            clip["file"] = f"{clip['id']}.mp4"
            spec = proj.spec_from_dict(clip, cfg)
            base_out = os.path.join(output_dir, spec.file)
            export_out = os.path.join(output_dir, f"{spec.id}.export.mp4")
            try:
                render.render_spec(cfg, proxy_path, spec, proxy_transcript, watermark,
                                   base_out, include_overlays=False)
                render.render_spec(cfg, proxy_path, spec, proxy_transcript, watermark,
                                   export_out, include_overlays=True)
                clip["render_status"], clip["needs_render"] = "done", False
                clip["export_status"], clip["export_file"] = "done", os.path.basename(export_out)
                from .jobs import publish_clip
                publish_clip(output_dir, clip["id"], export_out)
                clip["export_sig"] = _server._export_sig(clip)
                clip["export_spec"] = {key: clip.get(key) for key in _server.EXPORT_FIELDS}
            except Exception as exc:  # keep the project/editor available even if one render fails
                clip["render_status"], clip["render_error"] = "error", str(exc)
                log(f"[youtube] render failed for {clip['id']}: {exc}")

        proj.save_project(output_dir, project)
        from .jobs import _write_md
        _write_md(output_dir, project)
        completed = True
        progress("YouTube highlights are ready to edit", 1.0)
        return output_dir, project
    finally:
        if analysis_transcript_path and os.path.exists(analysis_transcript_path):
            try:
                os.remove(analysis_transcript_path)
            except OSError:
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)
        if not completed:
            shutil.rmtree(output_dir, ignore_errors=True)
