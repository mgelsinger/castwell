# Validation record

These checks establish working application flows; they do not establish universal advertisement detection.

## October 11 NVIDIA CUDA desktop release

The final 0.5.1 Windows checkout passed **688 tests and 121 subtests** in 46.45 seconds. Two symlink tests skipped because the Windows account cannot create symbolic links. JavaScript syntax and whitespace checks passed. The suite includes CUDA-only speech configuration, refusal to retry on CPU, GPU VAD provider checks, library locking, owned desktop service shutdown, portable storage, and installer artifact boundaries. A real frozen GUI check exposed a Windows JSON file-sharing race; bounded atomic-write retries and recurring GUI callbacks now preserve lifecycle control when a report reader temporarily locks the file. GPU runtime validation runs in a bounded owned child process, avoiding a frozen Tk worker startup hang and releasing the probe's CUDA contexts before classifier loading. One upstream Starlette test-client deprecation warning remains.

A frozen native launcher opened its real window, started the owned backend, tolerated repeated report reads, and stopped with exit code zero and a closed server port in a 6.4-second isolated check. Its PATH excluded external Python and FFmpeg. A separate frozen runtime check with CUDA devices masked returned a failure and no CPU fallback; the normal frozen check verified actual CUDA allocation, conversion kernels, float16, and GPU speech activity inference.

That frozen launcher loaded the exact pinned Qwen3.8 GGUF with **66/66 model layers offloaded to CUDA**, automatically selected `reviewed-qwen3.8-cuda`, and enabled verified two-pass reasoning and manual approval. One fixed three-segment classifier connection test returned valid structured results and identified its single explicit sponsored read. Startup took 23.344 seconds, classification 64.375 seconds, and owned shutdown 4.750 seconds; both server ports closed. The small-sample classification latency is a throughput limitation to measure on a full podcast. This is packaging/profile connectivity evidence, not a new accuracy benchmark, and used no paid provider.

Portable relocation fixes preserve the default library beside the executable and resolve original and cleaned audio within the moved library without rewriting its database paths. Regression checks cover a moved library's actual media responses and ZIP export, retained transcripts, approved cuts, revisions, favorites, and listening position. A copied library prefers its own media even while the old library exists. Custom data folders remain absolute selections. The final refreeze changed only the desktop and library modules; exact frozen speech, GPU, configuration, detector, and application module bytes and all 231 native runtime binaries match the earlier GPU-tested build. The final frozen CUDA runtime check also passed. Both distributions include the pinned Silero VAD MIT notice.

The final portable ZIP passed a real native GUI start/stop before and after moving its entire application folder and default `Data` library. The reopened app served byte-identical original and cleaned audio and retained its transcript, approved cut, favorite, and listening position. The old directory was not recreated. The same check verified CUDA allocation, conversion kernels, float16, GPU VAD, and the pinned license notice with external Python and FFmpeg excluded from PATH. No speech or classifier generation was repeated. The ZIP is 2,059,896,332 bytes with SHA256 `a395cbe9104d142b53bb0b56ee4a6d31ff63a885d4354a7009068dd0b4cdde81`.

The final installer passed current-user installation, shortcut and uninstall registration checks, positive CUDA runtime validation, a real native GUI start/stop, and its default `%LOCALAPPDATA%` library preference. It rendered a supplied 12-second synthetic recording to eight seconds, exercised local RSS download batches and a three-recording ZIP export, then rejected a separate startup with CUDA devices masked. Uninstall removed the application, shortcuts, and registration while preserving explicit, default, and GPU-rejection test libraries. All 1,861 installed runtime files matched the final portable archive, including the updated guide and pinned notices. The isolated workspace was removed. The installer is 1,301,319,285 bytes with SHA256 `27f9c8bca334de2e039da99d964ad323613803aa6dfe4d69d25fde7b834b6e6a`.

Before the storage fixes, both frozen distributions also completed actual CUDA transcription of a cached 21.18-second speech recording, alongside the same GUI, RSS, render, batch, ZIP, and uninstall checks. Speech and classifier generation were not repeated for the final storage changes because their frozen modules and native inputs were verified unchanged. The visibility-mask checks simulate GPU unavailability on this NVIDIA PC. The independent [hosted Windows build](https://github.com/mgelsinger/castwell/actions/runs/38173980629) completed package creation, installation, explicit GPU refusal, shortcut checks, and uninstall on a runner without an NVIDIA GPU, then failed during test workspace cleanup because the uninstaller had already removed a cached directory entry. The cleanup harness now tolerates only that missing-entry race; five filesystem regressions reproduce the original failure and verify that permission errors and unrelated deletion failures remain visible. The [Windows distribution workflow](https://github.com/mgelsinger/castwell/actions/workflows/windows.yml) also supports verification of existing artifacts after checking the current application source fingerprint, version, CUDA-only policy, package sizes, and SHA256 checksums. The [Checks workflow for the runtime commit](https://github.com/mgelsinger/castwell/actions/runs/38173980610) passed Python 3.10/3.12 tests, JavaScript syntax, Chromium desktop/mobile flows, and hour-long batch audio export.

The [follow-up hosted Windows verification](https://github.com/mgelsinger/castwell/actions/runs/38176123640) reused that previously built application after matching its source fingerprint and package checksums. Installation, explicit GPU refusal, shortcuts, uninstall preservation, and workspace cleanup passed with the corrected harness. The [Checks workflow for the cleanup revision](https://github.com/mgelsinger/castwell/actions/runs/38176123637) also passed Python 3.10/3.12 tests, browser flows, and hour-long batch audio export. These hosted packages exercise absence of NVIDIA hardware; the separately published packages are the exact local builds with the positive RTX 3090 Ti evidence and hashes recorded above.

On the RTX 3090 Ti, a read-only CUDA float16 transcription of the full 1,060.441-second Skeptoid recording completed in **22.515 seconds**, producing 229 segments and 2,538 word timestamps. ONNX profiling recorded only `CUDAExecutionProvider` for the speech activity model's neural operations, with CPU graph fallback disabled. The original SHA256 was unchanged and the live library was not modified. This checks real GPU execution and full-recording handling; transcription and ad-boundary accuracy were not scored in this packaging run. No paid classifier requests were made.

The CUDA Docker image built successfully and transcribed a cached 21.18-second speech fixture using the GPU speech activity model and Whisper. The container ran with an NVIDIA GPU and read-only model/audio mounts. Its normal CLI service also started with an isolated empty library as UID 10001, passed health and idle-queue checks through a host loopback port, and stopped cleanly. The Windows packages likewise require NVIDIA CUDA and do not offer a CPU inference fallback. Classifier and speech weights remain separate downloads; the bundled speech activity model is included.

After the relocation fixes, the final CUDA Docker image was rebuilt and passed its normal service health, empty queue, UID 10001, actual CUDA runtime, and GPU VAD checks with container networking disabled. The temporary container stopped and was removed. No model downloads or classifier calls were requested.

The source suite was restored with its reviewed CUDA profile and left running with an idle queue. All 7,019 episode rows, four feeds, eight podcast shelves, cut history, migrations, and six original recording hashes match the backup. Four authorized reviewed-profile settings were added; the existing settings row was unchanged. No paid classifier requests or live-library processing jobs were submitted during this packaging validation.

## October 11 podcast suite checks

The 0.5.0 Windows checkout passed **627 tests and 121 subtests** in 44.99 seconds. The final Chromium smoke passed in **40.4 seconds**, including mixed keep/remove podcast preferences, batch preparation and rendering, actual ZIP downloads, listening state, description preservation, and desktop/mobile layouts. A 65-episode catalogue verified 48-card paging, selection across unloaded pages, conditional 200/304 polling, and large-batch confirmation. A deterministic held-save regression reproduced a Linux CI failure before the fix and confirmed that completed actions now restore batch, podcast, and detail controls even after an unchanged catalogue poll. JavaScript syntax checks passed. One upstream Starlette test-client deprecation warning remains.

The isolated hour-long workflow passed in **25.125 seconds** with generated tones, supplied transcripts, and zero model calls. The keep recording decoded to 3,600 seconds and the edited recording to 3,540 seconds after approval of a known 60-second cut. Both originals retained their SHA256 hashes. The streamed ZIP measured 186,005,740 bytes; its temporary files occupied 788,501,534 bytes at the pre-cleanup measurement, and the temporary library and export workspace were removed. This tests full-length audio handling, approvals, transcript timelines, and exports; the elapsed time excludes speech recognition and AI classification. GitHub CI now repeats this gate.

A read-only browser check of the installed 7,019-episode catalogue opened its initial 48 cards in 0.953 seconds on this PC, expanded to 96, and showed eight shelves without mobile overflow or browser errors. The catalogue response measured 7,578,271 bytes; an unchanged conditional request returned an empty 304. These are local observations, not general performance guarantees. Migration and restart checks preserved the original five episode rows, nine cut revisions, and saved settings. New subscriptions were added separately. The installation checker passed the loopback listeners, local model prerequisites, database/media integrity, and free-storage checks within its manual-review scope.

Windows PowerShell 5.1 and PowerShell 7 both passed launcher ownership validation. The suite completed an actual stop/start cycle, rejected an altered process fingerprint, and refused to stop active episode work without explicit cancellation. The [production guide](production.md) documents these controls, backups, release gates, and the listening acceptance test. Full-podcast ad accuracy remains a separate acceptance requirement; no missed-ad or overcut guarantee is made.

The 0.5.0 wheel passed isolated import and CLI-help checks; all 19 packaged source/assets matched the checkout, README metadata matched, and private libraries, recordings, secrets, and weights were excluded. The 148,012-byte local artifact has SHA256 `71e420a7ca00a4deffa12382f3deaee5be95d7a8ed0264c83b9678b0593ae315`. This identifies this build, not a reproducible-build guarantee.

## October 8 v8 checks

The v8 Windows checkout passed **465 tests and 121 subtests** in 27.21 seconds. The application was restarted with the v8 policy, reasoning enabled and approval required for every cut. The final 0.4.0 wheel passed isolated import and CLI-help checks with policy `intent-boundary-v8`; all 17 packaged source/assets match the checkout, the locked README is in its metadata, and private recordings, libraries and models are excluded. The 121,862-byte wheel has SHA256 `8b7747a51152b3e3cd551809653ddda0f0d7f1be520d7c1445b93f18dbf8d964`. This identifies the local validation artifact, not a reproducible-build guarantee. The [Checks workflow](https://github.com/mgelsinger/castwell/actions/workflows/ci.yml) runs Python 3.10/3.12 tests, JavaScript syntax checks and Chromium desktop/mobile smoke tests. Its result should be checked for the exact Git revision being installed. Detection evidence is reported separately in the [readiness report](readiness-2026-10-08.md); test counts do not establish classifier accuracy.

The v8 Chromium smoke test passed in **26.8 seconds**, and JavaScript syntax validation passed. The UI suppresses machine-precision float tails for display while preserving stored cut boundaries. The regression verifies that selection and saving retain exact noisy values and detector metadata, boundaries with meaningful additional precision remain unchanged, and a manual edit clears approval while retaining the original detected bounds and evidence. A separate read-only check of real samples with 36 and 46 suggestions passed on desktop and a 390-pixel mobile viewport, with no database changes, writes, playback or browser errors. Screenshots containing real transcripts remain private. Frozen detector sources and fixtures were unchanged by this UI polish.

## October 8 v7 application checks

The v7 Windows checkout passed **436 tests and 121 subtests** on Python 3.12 in 27.94 seconds. The Chromium smoke check passed in 25.4 seconds, including persistence of the optional reasoning setting, raw detector-score labels, review and bulk selection, manual-boundary provenance, desktop/mobile layouts, and actual FFmpeg exports. The first browser attempt required the test to expand the advanced settings disclosure before interacting with its checkbox; after that test correction, the complete check passed. One existing upstream Starlette/httpx warning remains.

The README's ad-review screenshot, GIF and MP4 were refreshed from this interface using `scripts/record_demo.py`. The isolated synthetic library exercised an eight-second approved cut from 24 seconds of generated tone audio, a real FFmpeg export, and the cleaned transcript. The silent MP4 is 1280 by 848 at 24 fps for 26.08 seconds; the looping GIF is 960 by 636 at 8 fps. No speech model or classifier model was called. These assets illustrate the review controls and raw score wording, not detection accuracy.

An isolated PEP 517 wheel build, isolated import and CLI-help check passed. All 17 packaged source/assets matched the checkout, with no private library or model files included. That preliminary wheel predates the final documentation edits, so its hash is not presented as the final publication artifact. A `--no-build-isolation` attempt lacked setuptools in the main environment; the normal isolated build supplied its build dependencies successfully.

These software checks do not establish detection accuracy. The complete dense v7 inference and v8 selection replay are reported in the [readiness report](readiness-2026-10-08.md). Earlier fixed validation records follow.

## October 8 v6 review workflow

The final v6 Windows checkout passed **403 tests and 117 subtests** on Python 3.12 in 26.69 seconds, including recovered-speech safeguards, source-ID evidence resolution, bounded subdivision to individual targets, sentence assembly, retry/resume behavior, and the private recording evaluator. The Chromium smoke check passed with preserved cut metadata, manual boundary edits, bulk selection and clearing, original/cleaned playback positions, transcript-gap navigation, actual audio exports, and desktop/mobile layouts. The browser code was unchanged during v5/v6 work, so its passing 26.3-second run was retained. One upstream Starlette test-client deprecation warning remains.

A separate real-recording export check verified retained PCM samples, word order/timestamps, caption timelines, output durations, and unchanged source hashes. Model comparisons and their limitations are documented in the [October 8 readiness report](readiness-2026-10-08.md). The dated runs below describe earlier versions and are retained as history.

The final 0.4.0 wheel built successfully with isolated PEP 517 build dependencies. All 17 packaged source/assets matched the checkout byte for byte and the detector/evaluator files matched the frozen v6 candidate; private models, recordings, libraries and tests were excluded. An isolated installation imported the v6 modules and passed `python -m castwell --help`. The 119,791-byte wheel's SHA256 is `62142f27054b2f837e24c4115bcce16a1ce44a3db55d24adad95b9c6db627cf9`. This identifies the local validation artifact, not a reproducible-build guarantee.

The Qwen3.5 setup passed offline source/output verification and independent checks rejecting 19 corrupted provenance cases. PowerShell argument validation and Python 3.10 syntax checks passed without starting additional model servers. The final full runtime suite was run on Python 3.12; syntax compatibility alone is not a Python 3.10 runtime test.

## Windows publication check

On 2026-10-04, the local Windows checkout passed **133 automated tests and 116 subtests** with Python 3.11.15 and system FFmpeg. The JavaScript syntax check and the complete Chromium smoke test also passed, covering desktop and 390-pixel mobile layouts, uploads, waveform generation, original and cleaned playback, real audio exports, cut history, downloads, and subscriptions. The suite reported one upstream Starlette test-client deprecation warning.

This run exposed and corrected a Windows waveform issue: socket selectors cannot read anonymous FFmpeg pipes on Windows. Waveform decoding now reads bounded chunks in a worker while the caller continues to check for cancellation. A regression test verifies cancellation and child cleanup even when a decoder produces no output. Test fixtures also close SQLite handles explicitly and use portable subprocess failure simulations.

The new [GIF and video tour](media/README.md) were captured from the running application with an isolated synthetic library, generated tone audio, and an imported transcript. They exercise real local-rule detection, cut approval, FFmpeg export, and playback. No speech model or classifier model was downloaded or evaluated during this check.

## Castwell verification

On 2026-10-04, the renamed Castwell package passed all 132 automated tests and 116 subtests on Python 3.12. Both `python -m castwell` and the installed `castwell` command were checked. The isolated Chromium smoke test passed again with Castwell branding across the desktop and 390-pixel mobile layouts, including actual audio playback and rendering. The [README gallery image](images/gallery.png) is an unmodified screenshot from that run using local fixtures; transient notifications were allowed to expire before capture.

The renamed Docker image also built and passed a fresh-data smoke check as the unprivileged `castwell` user (UID 10001). Package metadata, startup, transcription imports, waveform generation, reviewed audio export, original-file preservation, and trusted-host validation passed. Its packaged Python and static assets matched the checkout.

The live model measurements below were recorded before this branding change. They remain evidence for the unchanged processing implementation, not a claim that model accuracy was remeasured during the rename.

## Application checks

These initial checks were performed on 2026-10-03 with Python 3.12 and CPU inference.

- 132 automated tests and 116 subtests passed. These include real FFmpeg rendering, API workflows, migrations, cancellation, review history, uploads, and request protection. One dependency deprecation warning remains in Starlette's HTTPX test-client adapter.
- The isolated Chromium smoke test passed for desktop and 390-pixel mobile layouts, including real playback and downloads, edits and restored revisions, subscriptions, and listening state.
- The Docker image built and ran as UID 10001. An isolated container accepted audio, rendered approved cuts, generated a waveform, served the result, and preserved the original bytes.
- The cloud install script completed, loaded Whisper small, verified the pinned classifier artifacts, and preserved existing preferences. The gallery and local classifier were started and checked through HTTP.

CI defines Python 3.10 and 3.12 jobs plus the browser smoke test. Only Python 3.12 was executed in this cloud session; the configured GitHub workflow has not been claimed as a completed remote run.

## Live classification checks

The local classifier was Qwen 2.5 7B Instruct Q4_K_M, using the pinned files in `scripts/local_ai.py`, llama-cpp-python 0.3.16, four CPU threads, and an 8192-token context. The portable CPU build avoids relying on a particular machine's advanced instruction set.

The six original examples in `tests/fixtures/classifier_cases.json` cover a host-read ad without a bumper, explicit sponsorship, ordinary show credits, a paid self-promotion, editorial product discussion, and quotation of advertising as an editorial topic. The 7B model matched the expected selections in five cases. In the host-read example it also selected the following editorial sentence at a reported confidence of 0.95. This is a small sanity check, not an accuracy benchmark.

Reproduce the semantic check with a running compatible endpoint:

```bash
CASTWELL_AI_WINDOW_CHARS=3000 CASTWELL_AI_CONTEXT_SEGMENTS=3 \
  python scripts/evaluate_classifier.py --output /tmp/castwell-classifier-results.json
```

## Full speech-to-export check

An original 23.86-second synthesized recording combined a discussion of trees in winter, a fictional coffee endorsement and discount offer without an opening bumper, sponsor thanks, and a return to the forest discussion. It was uploaded through the API and prepared without importing a transcript.

Whisper small recognized the brand more consistently than Whisper base. With small, the complete prepare flow took 54.98 seconds on this machine, using a 2000-character classifier window and three neighboring context segments. The classifier suggested 9.64–19.52 seconds, missing the opening endorsement at 6.80–9.28 seconds. Review mode correctly left the suggestion unapproved and did not produce an automatic export.

After manually adjusting the cut to 6.80–19.52 seconds through the API, rendering took 0.53 seconds and removed 12.72 seconds. The encoded MP3 measured 11.232 seconds, including encoder padding. The original SHA256 stayed unchanged. Retranscribing the output retained the editorial speech and found none of the commercial speech; the cleaned transcript export also excluded it. This verifies the complete review-and-export workflow, including its manual correction step.

**Review every suggestion remains enabled in the prepared cloud library.** Confidence scores did not prevent either incomplete detection or an overextended boundary. Evaluate representative episodes and listen around cuts before relying on automatic approval; the same results are not guaranteed for different speakers, languages, ad styles, or models.
