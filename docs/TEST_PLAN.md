# ZonClip test plan

This checklist is for the first Windows-machine test after pulling the latest `main`.

## Test environment

- Windows 10/11
- Python 3.11 or 3.12
- FFmpeg and FFprobe available on PATH (or the configured WinGet installation)
- A short MP4 recording stored inside the configured Videos library
- At least 5 GB free for the first small test; model downloads and render caches need additional space

Do not put API keys, OAuth tokens, private recordings, or generated clip files in the repository.

## 1. Install and launch

1. Open PowerShell in the repository's `clipper` folder.
2. Run `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.
3. Confirm setup completes without a red error and reports Python 3.11 or 3.12.
4. Launch `ClipForge.bat` (the app window should display **ZonClip Studio**).
5. Confirm the Home screen loads and lists MP4 files from the configured library.

If setup fails, save the full PowerShell error text. Do not delete the existing `%USERPROFILE%\clipforge` folder until its contents have been backed up.

## 2. First local clipping run

1. Choose a short recording (about 3–10 minutes) for the first run.
2. For a small first run, use the CLI from the `clipper` folder with `clip.py "<path-to-video>" --count 2 --limit-secs 300`. To test from the Home screen instead, set `"clip_count": 2` in your local `config.json` before launching; the Home screen currently uses the configured default count.
3. Wait for transcription, highlight detection, and rendering to finish.
4. Confirm the new `<recording name> - clips` folder contains `project.json` and rendered MP4 previews.
5. Confirm the `rankings` subfolder contains score-sorted compilation MP4s and `ranking_manifest.json` (unless you used `--no-rankings`).
6. Open the project in the Studio and play each generated clip.

Expected: processing works without an Anthropic API key. The default transcription model is `small.en`; the first run downloads it and later runs use the local cache.

## 3. Editor and captions

1. Select a clip and play it from the beginning.
2. Check that the active caption word follows the spoken audio.
3. Change a caption setting and wait for the preview to update.
4. Add a text element and an arrow, move them, then save.
5. Export/download the clip.
6. Open the exported file outside ZonClip and compare it with the Studio preview.

Expected: the exported video includes the selected captions and overlays once (not duplicated). If the preview is stale, use the Studio's reload control and export again.

## 4. Posting (optional)

Do this only after the local render/export tests pass.

- Connect YouTube and/or TikTok using your own developer app credentials.
- Start with a single test clip.
- Check the queue state and progress in the Posting panel.
- YouTube projects that have not completed Google's audit may be restricted to private uploads.
- TikTok's inbox/draft flow may require opening TikTok and completing the post manually.

Do not assume a successful API upload means a clip was publicly posted.

## 5. Report a failure

Include:
- Which numbered step failed
- The exact error text
- Whether it happened during setup, transcription, detection, rendering, editor save, export, or posting
- The relevant log excerpt from `%USERPROFILE%\clipforge\clipforge.log` (remove personal paths or account details if sharing publicly)

Never share OAuth access tokens, refresh tokens, client secrets, or API keys.

## Implemented features vs. validation status

The current repository is a **local Windows desktop application**. The codebase includes local transcription and highlight scoring, vertical rendering, word-by-word captions, the editor, long-form VOD rendering, YouTube/TikTok posting workflows, OBS-segment live mode, ranking compilations, and an Instagram Reels integration.

Passing unit tests or GitHub Actions does **not** mean every feature has been verified end-to-end. Use these distinctions:

- **Covered by automated regression tests:** local server request/path boundaries, live file stability and deduplication, live retry/one-shot behavior, ranking group selection, and Instagram OAuth URL/state handling.
- **Requires a real Windows-machine test:** setup on the target PC, FFmpeg rendering from a real VOD, caption timing and export comparison, long-form rendering, sustained OBS segment processing, and recovery after closing/reopening the app.
- **Requires the user's own platform setup and eligible account:** YouTube/TikTok/Instagram OAuth and uploads. A passing OAuth unit test does not prove that a real platform app has the required permissions or that a real upload will be accepted.
- **Not included:** direct ingestion of a Twitch/YouTube stream URL, cloud rendering/storage, a hosted dashboard, or bypassing platform audits/permissions. Live mode watches completed local OBS recording segments.
- **Not guaranteed:** fully hands-off public posting. Platform app review, account eligibility, upload privacy, inbox/draft behavior, and quotas are controlled by each platform.

The GitHub Actions checks are automated code checks. They do not replace the real-video and real-account tests above. Do not describe the application as production-ready until those machine- and account-dependent checks have passed.
