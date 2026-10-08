# Validation record

These checks establish working application flows; they do not establish universal advertisement detection.

## October 8 review workflow

The final v5 Windows checkout passed **403 tests and 117 subtests** on Python 3.12 in 25.72 seconds, including recovered-speech safeguards, source-ID evidence resolution, bounded subdivision to individual targets, sentence assembly, retry/resume behavior, and the private recording evaluator. The Chromium smoke check passed with preserved cut metadata, manual boundary edits, bulk selection and clearing, original/cleaned playback positions, transcript-gap navigation, actual audio exports, and desktop/mobile layouts. The browser code was unchanged during v5 work, so its passing 26.3-second run was retained. One upstream Starlette test-client deprecation warning remains.

A separate real-recording export check verified retained PCM samples, word order/timestamps, caption timelines, output durations, and unchanged source hashes. Model comparisons and their limitations are documented in the [October 8 readiness report](readiness-2026-10-08.md). The dated runs below describe earlier versions and are retained as history.

The final 0.4.0 wheel built successfully with isolated PEP 517 build dependencies. All 17 packaged source/assets matched the checkout byte for byte; private models, recordings, libraries and tests were excluded. An isolated installation imported the v5 modules and passed `python -m castwell --help`. The 119,581-byte wheel's SHA256 is `b2b7b0406c99f8c9fa2877843ae75eb8a8495c8e4f7c5cfedee1bb0244414922`. This identifies the local validation artifact, not a reproducible-build guarantee.

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
