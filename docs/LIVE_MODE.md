# Live clipping with OBS recordings

ZonClip live mode watches a local folder for completed OBS recording segments. Each segment is processed by the existing local transcription, six-signal highlight detector, vertical renderer, and word-by-word caption pipeline. New clips are added to one session-wide queue as each segment finishes.

There is **no session-wide clip limit**. The --count setting is only the maximum number of candidate clips requested from each segment; the detector may return fewer when it finds fewer candidates. The session queue grows across the whole broadcast.

## 1. Configure OBS

1. In OBS, open **Settings → Output → Recording**.
2. Set the recording format to **MKV** (safer if OBS or Windows closes unexpectedly).
3. Choose a dedicated recording folder, for example C:\ZonClip\LiveInput.
4. Enable automatic recording file splitting and choose a time-based split, initially **10 minutes**.
5. Start a short test recording and confirm OBS creates sequential files in that folder.

Use local recordings that you own or have permission to process. ZonClip does not download or capture a third-party stream URL; OBS is the local recording source.

## 2. Start the ZonClip watcher

Open PowerShell in the repository's clipper directory:

```powershell
$env:PYTHONPATH = (Get-Location)
& "$env:USERPROFILE\clipforge\.venv\Scripts\python.exe" .\live.py --watch "C:\ZonClip\LiveInput" --count 6 --settle-seconds 20
```

The watcher waits until each recording file has stopped changing for 20 seconds, then runs the existing clip.py pipeline against it. A segment is processed only once per session. The first run may take longer while the local transcription model is downloaded.

- --count 6: at most six candidate clips per completed segment, not six for the whole stream.
- --settle-seconds 20: wait for the recording file to be fully closed.
- --poll-seconds 5: how often to check the folder.
- --ranking-max-minutes 12: maximum duration of each ranked compilation file. More files are created as needed; this is not a cap on total output.

Press **Ctrl+C** to stop the watcher and render ranked compilation parts from all successfully processed clips.

## 3. Session outputs

The watcher creates a session directory containing:

- live_state.json: processed segment history and errors.
- highlight_queue.json: session-wide clips sorted by local score, with Top / Good / Ranking tiers.
- segments/<segment> - clips/: normal ZonClip projects and rendered clips for each segment.
- rankings/ranked_highlights_001.mp4, etc.: score-sorted compilations, split by the configured maximum duration.
- logs/: per-segment pipeline output.

The tier labels are relative to the session's detected clips. They do not claim that every moment is a highlight, and ZonClip never invents clips to fill a target.

## 4. Current limitations

- Live mode currently ingests **completed local OBS recording segments**, not a direct Twitch/YouTube stream URL.
- A segment must finish and remain stable before it is processed; live output therefore trails the broadcast by approximately one split interval plus processing time.
- Ranked compilation videos are finalized when you stop the watcher. Individual clips and the highlight queue update while the session is running.
- For the first test, use 5–10 minute recordings and a low per-segment count. This is CPU-heavy on older laptops.
- A crashed session can be inspected through live_state.json; already completed segments are recorded to avoid processing them again when restarting with the same session directory.

This is the local live-ingest milestone. It does not replace the existing VOD workflow.
