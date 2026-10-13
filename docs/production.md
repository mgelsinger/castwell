# Castwell production operation

Castwell prepares podcast batches with a per-show Remove ads or Keep original choice, CUDA transcription, and reviewed audio exports. Its production boundary is a trusted computer with one server worker, durable local storage, and manual approval of ad cuts. The release checks below verify software and installation readiness. Accurate ad removal is a separate release requirement; library and installer checks cannot satisfy it.

**The current ad-removal classifier has not passed production acceptance.** In the [full CUDA recording evaluation](evaluations/2026-10-11-full-stdwytk-cuda.html), classification took 106:34 for a 79:55 episode and still missed two ad openings and proposed an extra sponsor transition. The fake rutabaga read was preserved. Installation checks and successful exports do not make that performance acceptable for routine batch processing. Keep every cut subject to review until the replacement workflow has measured speed and accuracy evidence, followed by listening verification.

The source candidate now connects compact scene detection with focused endpoint review to the app and CUDA launchers. The frozen fresh test used 44 real classifier requests across one complete recording and two unused tails. The 75:13 complete recording took 7:19 to classify, with a separate 4:44 for CUDA large-v3 transcription, but missed 42.60 seconds of commercial word duration and proposed 11.96 seconds of protected word duration. The Skeptoid tail missed a further 4.62 seconds of promotional word duration. All cuts remain unapproved; this version has not passed production acceptance. See the [ad-removal plan](ad-removal-production-plan.md).

## Start the suite on Windows

For the portable app and full installer, follow [Windows desktop installation](windows.md). Both bundle CUDA inference runtimes and require NVIDIA hardware. The desktop launcher owns its services, verifies GPU execution, and stops instead of switching inference to CPU. Speech weights and classifier weights remain separate.

Use the installed project environment and pinned native llama.cpp/Qwen3.8 profile described in [local models](local-models.md). From the repository directory:

```powershell
.\scripts\start_suite.ps1 -ReviewedProfile
```

The source launcher honors every saved or explicitly selected speech model. A new library with `-ReviewedProfile` defaults to `large-v3`, the speech model used in the complete-recording test; other new source profiles retain `small.en`. `-ReviewedProfile` selects the compact scene and endpoint candidate with fixed bounded stage settings and manual approval by default; an explicitly saved approval preference persists. Model weights and FFmpeg must already be installed; the suite launcher does not download them or spend money on a provider. It verifies pinned model/runtime files, checks existing process ownership before reusing ports, and writes service state under `.local/runtime`.

The owned classifier sleeps between tasks so transcription can use the GPU. Castwell waits for that sleep state before loading Whisper and releases Whisper after transcription before the classifier wakes. Explicit speech-model preparation uses the same resource lock, releases cached speech weights, and waits for the owned classifier to sleep. Preparation and episode processing cannot be admitted concurrently; downloads and rendering remain available. Neural speech recognition, voice activity checks, and local classification require CUDA. Ordinary decoding and file operations can use the host processor.

Open `http://127.0.0.1:8000`. Select a podcast and choose **Remove ads** or **Keep original**. Removing ads prepares suggestions for your review; it does not promise that every commercial will be recognized. Keeping ads prepares transcripts and retains the original recording. The saved podcast choice applies when you queue episodes, so changing it later does not alter an already queued job.

Select episodes or all episodes in a podcast to download, prepare, retry, cancel, export approved cuts, update listening state, or download an archive. Preparation and download selections of 50 or more episodes show the total and require confirmation before queueing. Review boundaries before exporting cleaned audio. A batch reports each item so successful work remains usable when another episode fails. ZIP exports allow up to 500 episodes and 10 GiB of audio and transcript payload, plus manifest and container headers. Oversized transcript attachments are omitted with a report entry while their audio remains included.

`-ValidateOnly` verifies the installed prerequisites without starting services. `-Port`, `-ClassifierPort`, `-DataDirectory`, `-ModelCache`, and `-SpeechModel` support an existing custom installation. The suite never downloads speech weights during startup. Prepare the selected model explicitly if it is missing.

Stop the recorded services while the queue is idle:

```powershell
.\scripts\stop_suite.ps1
```

The stop command verifies recorded process IDs, creation times, and commands before stopping them. It refuses active jobs by default. `-CancelJobs` first requests cancellation of active jobs; completed artifacts remain available for retry. `-ValidateOnly` checks ownership without stopping services.

## Run the installation check

Keep Castwell and the local classifier running with no queued episode jobs, feed refresh, or speech-model preparation. Use the same data directory as the server:

```powershell
.\.venv\Scripts\python.exe scripts/check_release.py `
  --app-url http://127.0.0.1:8000 `
  --data-dir D:\Projects\castwell\data `
  --require-local-ai `
  --output .local\release-readiness.json
```

The checker returns exit code `0` when its operational checks pass and `1` when a blocker remains. Its JSON includes the exact time, installed app version, individual results, library counts, and free storage. `--base-url` and `--json` are aliases for the URL and report arguments. The default reserve is 20 GiB; increase `--minimum-free-gb` for a larger planned batch. A recording, transcript, temporary decoded audio, and cleaned export can coexist, so compressed download sizes alone are insufficient for capacity planning.

The command makes only local GET requests, with a 32 MiB limit per JSON response for larger catalogues. It checks app health, podcast APIs, dependencies, cached transcription files, manual approval settings, actual app and classifier listen addresses, idle queue and background operations, SQLite integrity, stored cut ranges, referenced recordings, matching episode IDs, and available storage. A configured local classifier is checked through its model list, without inference. Hosted classifiers are never contacted. Reports omit transcripts, titles, subscription addresses, credentials, and stored media paths.

`ready: true` means the installation checks passed within the stated local scope. The report separately records `production_ready: false` when ad-removal acceptance has not been measured by that check. It does not validate model weights by inference, prove word alignment, simulate an interrupted download, or establish internet-facing security.

Unapproved cuts, unresolved detector decisions, or uncertain transcription coverage keep a Remove ads episode pending. A manual partial export can be previewed, but the original remains its preferred listening copy and it is excluded from completed preferred exports. Listen to every flagged audio passage, add or adjust missed ads, and save before choosing **I've listened and addressed any missed ads**. That explicit action records your report of listening; it does not claim model verification or automatically render audio. Changes to the original or transcript invalidate that acknowledgement.

## Release verification

Run once after changing code or processing behavior:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check castwell/static/app.js
.\.venv\Scripts\python.exe scripts/smoke_browser.py --artifacts .local\suite-browser-check
.\.venv\Scripts\python.exe scripts/smoke_long_episode.py --output .local\long-episode-check\report.json
```

The browser check uses an isolated temporary library, synthetic audio, imported transcripts, local RSS, and the rule detector. It runs real FFmpeg exports and checks desktop/mobile behavior. It does not access your listening library, download weights, contact paid services, or measure speech-model accuracy. GitHub Actions runs this fixture suite and retains browser screenshots on every push.

The long-episode gate generates two 60-minute recordings, applies mixed podcast preferences, approves a known 60-second cut, and streams an audio ZIP to disk. It checks decoded durations, transcript timelines, original hashes, and temporary-file cleanup with zero model calls. Its temporary library requires about 1 GiB of free storage and is removed after the report is saved. This validates full-length handling; it does not measure ad detection accuracy.

## Full podcast acceptance

Before using cleaned audio as your normal listening copy, test a complete episode from each show you use. Include a genuine host-read ad, an unpaid fake ad such as the rutabaga parody, and an episode with no commercial. Feed advertisements can vary by region, date, or download, so preserve the exact original used for the test.

1. Start with manual approval enabled. Set one show to **Keep original** and another to **Remove ads**, then queue both in one preparation batch.
2. Check that both produce playable originals and searchable transcripts, that the keep choice skips ad detection, and that the remove choice produces reviewable proposals or a clear error. Measure the elapsed time and available storage for the full batch.
3. Listen to the complete original while comparing its transcript and proposals. Record every real ad missed, every editorial or parody passage proposed, and boundary errors at the start and end of each read. A high model confidence does not substitute for this check.
4. Approve only the intervals you intend to remove. Export the batch and inspect the archive report: keep episodes must use originals; cleaned episodes must contain only your approved cuts; unavailable requested versions must be reported.
5. Listen around every splice and check the cleaned audio beginning and ending. Compare the removed duration with your selections and confirm the original is still playable.
6. Restart Castwell while idle. Confirm show choices, favorites, played state, playback positions, transcripts, cut revisions, and prepared recordings remain available. Repeat the installation checker.

For unattended removal, the current evidence is insufficient. The [matched model comparison](standardized-model-comparison-2026-10-08.md) and [policy pilot](policy-pilot-2026-10-09.md) include missed commercial speech and incorrectly selected editorial/parody speech. The v9 experiment was withheld because it introduced a commercial boundary regression. The 0.5.2 candidate selects Qwen3.8 with compact scenes and endpoint review, subject to manual approval; the earlier v8 setup remains historical evidence. Speech gaps, music, blended promotions, jokes, and inaccurate timestamps all need listening judgment.

## Backup and recovery

Before upgrading, wait for the queue to become idle, stop Castwell, and copy the **entire data directory** to a private backup location. This includes `library.sqlite3`, any SQLite WAL files, originals, and cleaned media. Copy an external speech-model cache separately. Preserve the installed classifier model and runtime provenance so the same inference profile can be restored.

Backups can contain premium feed addresses and private transcripts. Keep them private. Store a backup outside the live library and verify its files before relying on it. A database-only snapshot omits audio.

Restore to the same absolute data-directory path because stored media paths are absolute. Keep the unsuccessful directory separately, stop the server, restore the full verified copy, and restart against it. Run the readiness checker before resuming batches. Do not downgrade code against an upgraded live database; use the code and complete backup from the same release.

Interrupted jobs are retained for retry. A retry reuses completed recordings and valid transcripts rather than discarding them. Regenerating a transcript or redetecting advertisements is an explicit action and can take the same time as fresh processing. When only a feed item fails, check the batch result and retry that item; do not remove successful library records to rerun a batch.

## Deployment scope

Run one Castwell worker per data directory on a local filesystem suitable for SQLite. Bind the app and model server to loopback. There is no login or account separation. The host allowlist and cross-origin request checks are useful protections for the local application; they do not supply authentication, TLS, or authorization for public hosting. A deployment shared beyond the trusted device requires separately configured authenticated private access and its own security review.

Keep logs under `.local` and monitor free space before large downloads. RSS refresh is manual. No recurring feed polling, background purchase, or automatic paid-model fallback is enabled by these release steps.
