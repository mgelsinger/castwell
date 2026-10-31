# Current-policy fresh CUDA checks

Fast v10 processed the complete August Stuff They Don't Want You to Know recording in 6:25.73, or 4:49.60 per audio hour, meeting the five-minute goal on the local RTX 3090 Ti. The owner chose to continue packaging and defer the July v10 run after a GPU-memory preflight block. July has no current-v10 timing or output-equivalence result. Earlier v9 measurements remain visible for comparison.

| Policy | Recording | Audio length | Total including cleanup | Time per audio hour | Fresh transcription | Detection and classifier startup | Rendering | Five-minute target |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| v9 | July 30 | 75:13.12 | 5:58.12 | 4:45.67 | 81.83 s | 228.26 s | 36.28 s | Pass |
| v9 | August 27 | 79:55.01 | 6:16.62 | 4:42.76 | 85.41 s | 240.94 s | 37.59 s | Pass |
| v10 | August 27 | 79:55.01 | 6:25.73 | 4:49.60 | 90.83 s | 244.06 s | 32.56 s | Pass |
| v10 | July 30 | 75:13.12 | Deferred | Not measured | Not run | Not run | Not run | Not evaluated |

Each measured row is a complete fresh processing run, not a cached-transcript estimate. Totals include isolated database/waveform preparation, model setup, large-v3 transcription with coverage checks, both classifier passes, saved proposals, rendering and owned-service cleanup. V10 used the same model, context, reasoning budgets and maximum-request guard as v9. August v10 ran first to check the contextual-recommendation correction. These are consumed development recordings, not unseen accuracy holdouts.

Input recordings and weights were already local. Preflight hashing, duration probing and GPU inventory are excluded and recorded per run in the [machine-readable evidence](2026-10-31-current-policy-cuda.json); August v10 preflight took 16.40 seconds. UI interaction, queue scheduling and network downloads are also excluded. Model caches started empty, while operating-system disk caches were not flushed.

The configuration uses Qwen3.8-27B Q4_K_M, a 32,768-token context, 6,144 global reasoning tokens and 1,024 focused boundary reasoning tokens. Speech recognition uses large-v3, batch size 8 and float16 CUDA. Actual speech and voice activity execution used CUDA, CPU fallback was disabled for voice activity inference, and classification offloaded 66 of 66 layers. V10 August made 2 local generation requests, 6 across all three completed fresh runs, with zero paid calls, transcript reuse or prediction reuse. Earlier cached diagnostics are recorded separately in the [listening-correction report](2026-10-31-listening-corrections.md).

The completed July v9 run regenerated all 12,110 classification words with the same text and timestamps as the accepted correction candidate. Its word decisions, cut intervals and complete rendered MP3 matched that candidate exactly. This preserves the accepted first-break and closing excerpts for v9; it does not establish v10 July equivalence or full-episode listening acceptance.

Fast v9 selected a 7.50-second sister-show recommendation at 3450.05-3457.55 seconds within topical conversation. The next line referred back to it, making the interruption potentially disruptive. The owner chose to preserve these contextual recommendations, and the shared v10 policy received a generic distinction without episode names, brand names or timestamps.

August v10 retains that recommendation and one additional 1.62-second interval at the end of an advertisement: the three-word streaming-platform phrase at 390.10-391.72 seconds. Exactly 19 of 12,898 classification word decisions changed, 16 in the contextual recommendation and three in the ad ending. There are no newly selected intervals; every other cut remains the same. The extra ad ending is an explicit new remnant, not an accuracy pass. Consistent with the owner's existing tolerance for small remnants, no further prompt tuning, manual timestamp override or wider cuts were introduced.

The complete provisional rutabaga parody interval, 60.55-107.01 seconds, overlaps 0.00 seconds of proposed removal. August's first cut remains at 238.51 seconds, after the sketch and surrounding discussion. Across the historical protected units inspected, v10 selects 0 word entries (0.00 ASR word-seconds). The old combined production-credit/platform-invitation unit remains excluded from that comparison because its scope changed; its label was not rewritten. The neutral production attribution remains outside the cuts while the separate catalogue invitation is selected. The Haunted Library promo cuts remain unchanged from v9.

Historical product-read annotations leave 4.75 ASR word-seconds outside the August v10 cuts, versus 3.13 in v9. This consumed-transcript diagnostic is not measured audible advertising and does not establish the five-residual-ad-seconds-per-hour requirement. The new August decision audit has 0 unresolved words. Full-episode acoustic acceptance remains unverified.

The attempted July v10 run stopped at the unchanged 20,480-MiB free-GPU-memory preflight reserve while other applications occupied the GPU. It made zero ASR or generation calls and has no processing-time result. Its failure record is retained separately. The owner then deferred this run; no retry or background polling remains active. A separate retry script is prepared for when the owner resumes validation. No user applications were stopped and the reserve was not lowered.

GPU activity was sampled every 15 seconds. Ordinary desktop processes remained present, so these are observed system timings, not an isolated hardware benchmark. CPU/disk packaging work overlapped part of July v9 and August v10 setup/ASR. No second Castwell GPU task ran concurrently during the completed measurements. Driver version was 610.88.

Original recordings and the user's library were unchanged. Saved proposals remain unapproved; separate previews used temporary approved copies solely for validation. Owned classifiers stopped and speech caches were released. No extra padding or gap merging was added. Manual review remains required for the release candidate.
