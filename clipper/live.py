#!/usr/bin/env python
"""Continuous local clipping from completed OBS recording segments.

OBS should write time-split recordings into an input folder. ZonClip watches for
files that have stopped growing, processes each segment with the existing local
VOD pipeline, and keeps a session-wide highlight queue. There is no session-wide
clip-count ceiling; --count is only a per-segment compute budget.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".flv", ".ts", ".webm"}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, fallback: dict) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback.copy()
    except (OSError, ValueError):
        return fallback.copy()


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS and not path.name.endswith(".tmp")


def discover_ready(folder: Path, state: dict, settle_seconds: float, now: float | None = None) -> list[Path]:
    """Return files whose size has stayed unchanged for the configured settle period."""
    now = time.time() if now is None else now
    observed = state.setdefault("observed", {})
    ready = []
    present = set()
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not is_video(path):
            continue
        key = str(path.resolve())
        present.add(key)
        try:
            stat = path.stat()
        except OSError:
            continue
        signature = [stat.st_size, stat.st_mtime_ns]
        previous = observed.get(key)
        if not previous or previous.get("signature") != signature:
            observed[key] = {"signature": signature, "stable_since": now}
            continue
        if now - float(previous.get("stable_since", now)) >= settle_seconds:
            ready.append(path)
    # Forget deleted or renamed partial files.
    for key in list(observed):
        if key not in present:
            observed.pop(key, None)
    return ready


def tier_for_rank(rank: int, total: int) -> str:
    """Relative quality tiers; no artificial minimum number of clips is created."""
    if total <= 0:
        return "ranking"
    top_count = max(1, math.ceil(total * 0.20))
    good_count = max(top_count, math.ceil(total * 0.50))
    if rank <= top_count:
        return "top"
    if rank <= good_count:
        return "good"
    return "ranking"


def collect_highlights(session_dir: Path, state: dict) -> list[dict]:
    found = []
    for record in state.get("segments", []):
        project_path = Path(record["output_dir"]) / "project.json"
        try:
            project = json.loads(project_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            continue
        for clip in project.get("clips", []):
            if clip.get("render_status") != "done" or not clip.get("file"):
                continue
            mp4 = (project_path.parent / clip["file"]).resolve()
            if not mp4.is_file() or mp4.stat().st_size == 0:
                continue
            duration = sum(max(0.0, float(x.get("end", 0)) - float(x.get("start", 0)))
                           for x in clip.get("segments", []))
            found.append({
                "id": f'{record["key"]}:{clip.get("id", mp4.stem)}',
                "score": float(clip.get("score", 0) or 0),
                "duration": duration,
                "file": str(mp4),
                "source": record["source"],
                "source_rank": int(clip.get("rank", 0) or 0),
                "title": ((clip.get("metadata") or {}).get("shorts") or {}).get("title")
                         or clip.get("transcript", "")[:80],
            })
    found.sort(key=lambda item: (item["score"], item["duration"]), reverse=True)
    for rank, item in enumerate(found, 1):
        item["rank"] = rank
        item["tier"] = tier_for_rank(rank, len(found))
    return found


def write_concat_file(path: Path, clips: list[dict]) -> None:
    lines = []
    for clip in clips:
        escaped = Path(clip["file"]).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_ranked_videos(session_dir: Path, clips: list[dict], max_minutes: float) -> list[str]:
    """Build as many ranked parts as needed; max_minutes limits each file, not total output."""
    if not clips:
        return []
    ffmpeg = None
    try:
        sys.path.insert(0, str(ROOT))
        from pipeline import util
        ffmpeg = util.ffmpeg()
    except Exception:
        ffmpeg = "ffmpeg"
    output_dir = session_dir / "rankings"
    output_dir.mkdir(parents=True, exist_ok=True)
    groups, current, seconds = [], [], 0.0
    max_seconds = max(60.0, max_minutes * 60.0)
    for clip in clips:
        duration = max(0.1, float(clip.get("duration") or 0.1))
        if current and seconds + duration > max_seconds:
            groups.append(current)
            current, seconds = [], 0.0
        current.append(clip)
        seconds += duration
    if current:
        groups.append(current)

    outputs = []
    for index, group in enumerate(groups, 1):
        concat_path = output_dir / f".concat_{index:03d}.txt"
        output_path = output_dir / f"ranked_highlights_{index:03d}.mp4"
        write_concat_file(concat_path, group)
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(output_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        try:
            concat_path.unlink()
        except OSError:
            pass
        if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
            detail = (result.stderr or "FFmpeg did not create an output file")[-1200:]
            raise RuntimeError(f"Could not render ranking part {index}: {detail}")
        outputs.append(str(output_path))
    return outputs


def process_segment(path: Path, session_dir: Path, per_segment_count: int) -> dict:
    key = path.stem
    output_dir = session_dir / "segments" / f"{key} - clips"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable, str(ROOT / "clip.py"), str(path),
        "--count", str(per_segment_count), "--out", str(output_dir),
    ]
    result = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
    log_path = session_dir / "logs" / f"{key}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text((result.stdout or "") + "\n" + (result.stderr or ""), encoding="utf-8")
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Clip pipeline failed for {path.name}; see {log_path}")
    if result.returncode == 1 and not (output_dir / "project.json").is_file():
        return {"key": key, "source": str(path.resolve()), "output_dir": str(output_dir),
                "status": "no_highlights", "log": str(log_path)}
    return {"key": key, "source": str(path.resolve()), "output_dir": str(output_dir),
            "status": "processed", "log": str(log_path)}


def run(args) -> int:
    source = Path(args.watch).expanduser().resolve()
    if not source.is_dir():
        raise SystemExit(f"Recording folder does not exist: {source}")
    session = Path(args.session).expanduser().resolve() if args.session else (
        source / f"ZonClip Live {time.strftime('%Y-%m-%d %H-%M-%S')}")
    session.mkdir(parents=True, exist_ok=True)
    state_path = session / "live_state.json"
    state = read_json(state_path, {"observed": {}, "processed": {}, "segments": []})
    print(f"[live] Watching completed recordings in: {source}")
    print(f"[live] Session output: {session}")
    print("[live] No session-wide clip cap. Press Ctrl+C to stop and build ranked videos.")
    try:
        while True:
            ready = discover_ready(source, state, args.settle_seconds)
            for path in ready:
                key = str(path.resolve())
                if key in state.setdefault("processed", {}):
                    continue
                print(f"[live] Stable recording detected: {path.name}")
                try:
                    record = process_segment(path, session, args.count)
                    state["segments"].append(record)
                    state["processed"][key] = {
                        "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns,
                        "status": record["status"], "processed_at": int(time.time()),
                    }
                    highlights = collect_highlights(session, state)
                    atomic_json(session / "highlight_queue.json", {
                        "updated_at": int(time.time()), "total": len(highlights),
                        "clips": highlights,
                    })
                    print(f"[live] {record['status']}: {path.name}; {len(highlights)} session highlights so far")
                except Exception as exc:
                    try:
                        stat = path.stat()
                        state.setdefault("processed", {})[key] = {
                            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                            "status": "error", "processed_at": int(time.time()),
                        }
                    except OSError:
                        pass
                    state.setdefault("errors", []).append({
                        "source": str(path), "error": str(exc), "at": int(time.time())})
                    print(f"[live] ERROR: {exc}", file=sys.stderr)
                atomic_json(state_path, state)
            atomic_json(state_path, state)
            if args.once:
                break
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("\n[live] Stop requested; finalizing ranked highlight videos...")
    highlights = collect_highlights(session, state)
    atomic_json(session / "highlight_queue.json", {
        "updated_at": int(time.time()), "total": len(highlights), "clips": highlights,
    })
    rankings = build_ranked_videos(session, highlights, args.ranking_max_minutes)
    state["ranking_videos"] = rankings
    state["stopped_at"] = int(time.time())
    atomic_json(state_path, state)
    print(f"[live] Finished: {len(highlights)} highlights, {len(rankings)} ranked videos.")
    print(f"[live] Open: {session}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch OBS split recordings and clip them locally as they finish.")
    parser.add_argument("--watch", required=True, help="Folder receiving OBS time-split recordings")
    parser.add_argument("--session", default=None, help="Session output folder (default: created inside watch folder)")
    parser.add_argument("--count", type=int, default=6, help="Maximum clips to ask for per completed segment (not a session cap)")
    parser.add_argument("--settle-seconds", type=float, default=20.0, help="How long a recording must stay unchanged before processing")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--ranking-max-minutes", type=float, default=12.0, help="Maximum length of each ranked compilation part")
    parser.add_argument("--once", action="store_true", help="Process currently stable files once, then exit")
    args = parser.parse_args()
    if args.count < 1 or args.count > 50:
        parser.error("--count must be between 1 and 50 per segment")
    if args.settle_seconds < 0 or args.poll_seconds < 1 or args.ranking_max_minutes <= 0:
        parser.error("settle must be >= 0, poll >= 1, and ranking max minutes > 0")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
