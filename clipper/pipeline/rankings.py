"""Build score-sorted ranking compilations from rendered ZonClip clips."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import util


def ranking_groups(clips: list[dict], group_size: int = 3) -> list[list[dict]]:
    """Sort successfully rendered clips by score and group them into ranking videos."""
    if group_size < 1:
        raise ValueError("group_size must be at least 1")
    ready = []
    for clip in clips:
        if clip.get("render_status") != "done" or not clip.get("file"):
            continue
        ready.append(clip)
    ready.sort(key=lambda item: (float(item.get("score", 0) or 0), int(item.get("rank", 0) or 0)),
               reverse=True)
    return [ready[i:i + group_size] for i in range(0, len(ready), group_size)]


def _write_concat(path: Path, files: list[Path]) -> None:
    lines = []
    for file in files:
        escaped = file.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_rankings(out_dir: str, project: dict, group_size: int = 3) -> list[dict]:
    """Create one ranked compilation for each group; total output grows with valid clips."""
    root = Path(out_dir).resolve()
    groups = ranking_groups(project.get("clips") or [], group_size)
    ranking_dir = root / "rankings"
    ranking_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, group in enumerate(groups, 1):
        files = [(root / clip["file"]).resolve() for clip in group]
        missing = [str(path) for path in files if not path.is_file() or path.stat().st_size == 0]
        if missing:
            raise RuntimeError("Ranking source clip is missing: " + ", ".join(missing[:3]))
        concat_file = ranking_dir / f".ranking_{index:02d}.txt"
        output = ranking_dir / f"ranking_{index:02d}.mp4"
        _write_concat(concat_file, files)
        command = [
            util.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(output),
        ]
        try:
            run = subprocess.run(command, capture_output=True, text=True)
        finally:
            try:
                concat_file.unlink()
            except OSError:
                pass
        if run.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            detail = (run.stderr or "FFmpeg did not create a ranking file")[-1200:]
            raise RuntimeError(f"Ranking video {index} failed: {detail}")
        results.append({
            "rank": index, "file": str(output.relative_to(root)).replace("\\", "/"),
            "clips": [clip.get("id") for clip in group],
            "scores": [float(clip.get("score", 0) or 0) for clip in group],
        })
    manifest = {"group_size": group_size, "total_clips": sum(len(x) for x in groups),
                "total_videos": len(results), "videos": results}
    (ranking_dir / "ranking_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return results
