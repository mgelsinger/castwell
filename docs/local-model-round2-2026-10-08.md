# Local model comparison, October 8, 2026

**Keep native llama.cpp with Qwen3.5-27B dense and Castwell's original v8 policy, with manual review.** Neither role-first prompt candidate qualified to replace it. Qwen3.8 missed commercial speech; Qwen3.5 role-first improved known examples but added protected speech on fresh episodes. Kev remains a separate optional comparison CLI, not the application's classifier or the official paid Jev API. All inference here was local and free.

The [Qwen numeric report](evaluations/2026-10-08-qwen-round2-comparison.json) and [Kev numeric report](evaluations/2026-10-08-kev-round2-comparison.json) preserve completed populations, failures, skipped suites, timings and provenance hashes. Detailed reference labels, timestamps, transcripts and model responses remain private.

## Known recordings

Five excerpts from four shows were already inspected during development. Corrected references contain **408.96 recognized ad-word seconds** and **1,996.35 protected-word seconds**. Every candidate below completed all five excerpts. Actual approved cuts were zero.

| Candidate | Ad-word seconds proposed | Ad-word seconds missed | Protected-word seconds proposed |
| --- | ---: | ---: | ---: |
| Existing Qwen3.5 v8, retained | 408.96 | 0.00 | 11.68 |
| Qwen3.5 role-first | 408.96 | 0.00 | 0.00 |
| Qwen3.8 role-first | 407.66 | 1.30 | 2.22 |
| Kev 4B matched control | 335.92 | 73.04 | 47.34 |
| Kev 9B original questions | 337.64 | 71.32 | 46.42 |
| Kev 9B verbatim targets | 394.58 | 14.38 | 56.92 |
| Kev 9B wider context | 325.06 | 83.90 | 51.26 |

Qwen3.8 missed a paid-event lead-in in Skeptoid and newly proposed interview/transition speech in DOAC. Reduced errors elsewhere do not cancel that recall regression. Qwen3.5 role-first had no per-unit proposal regression on this known set, raising verified ad-word coverage from 280.10 to 286.10 seconds with zero verified protected speech. That permitted further testing, not promotion.

All three Qwen configurations preserved the full known rutabaga sketch: 46.48 continuous seconds and 38.86 recognized word-seconds. Original Kev 9B and wider context instead proposed 33.50 continuous seconds of the protected sketch.

## Fresh episodes decide the recommendation

Three independently annotated excerpts from new episodes of *Stuff They Don't Want You To Know*, *Stuff You Should Know* and *Skeptoid* total **779.988 seconds**. Audio, ASR and reference populations were frozen before predictions. These are previously studied shows, not held-out hosts.

Primary scoring excludes two repeated ad-copy units. The secondary whole-excerpt view includes their separately frozen commercial labels. One unresolved relationship interval stays excluded in both. Every configuration completed all three excerpts without execution failures.

| Candidate | Primary ad-word seconds proposed / 32.72 | Ad-word seconds missed | Protected-word seconds proposed / 417.40 | Whole-excerpt ad-word seconds proposed / 83.02 |
| --- | ---: | ---: | ---: | ---: |
| Existing Qwen3.5 v8, retained | 32.72 | 0.00 | 16.30 | 83.02 |
| Qwen3.5 role-first, rejected | 32.72 | 0.00 | 26.92 | 83.02 |
| Qwen3.8 role-first, rejected | 26.92 | 5.80 | 16.30 | 77.22 |

The retained v8 still proposes **16.30 word-seconds of existing-member acknowledgment and navigation** in Skeptoid. Qwen3.5 role-first adds **10.62 protected word-seconds** of free-participation/editorial-credit speech, with no ad-coverage gain. Both fresh views therefore fail the fixed nonregression criterion. Qwen3.8 misses 5.80 ad-word seconds from a paid membership offer and retains v8's 16.30-second protected-speech error.

These false proposals are review-only. V8 and Qwen3.5 role-first have identical verified coverage: **28.90 of 32.72** primary ad-word seconds and **47.34 of 83.02** whole-excerpt ad-word seconds, with zero verified protected speech. Selecting only verified suggestions would leave ads behind. Actual approvals remain zero.

All three preserve the different fresh parody script: **38.56 continuous seconds and 31.48 recognized word-seconds**. This supports two tested scripts, not general parody accuracy. V8 selects 34.92 of 38.02 annotated commercial continuous seconds in the fresh primary population despite selecting every recognized ad-word interval. Listening remains necessary for gaps and exact cuts.

## Reference correction, reported separately

The original v3 labels accidentally included **1.28 word-seconds of an editorial return marker** in a Skeptoid ad. An explicit v4 correction moves those words to protected speech and leaves an adjacent untimed gap unlabeled. Predictions did not change.

Unchanged v8 proposals remain **408.96 of 410.24** ad-word seconds under original labels and **408.96 of 408.96** under corrected labels, with **11.68 protected-word seconds** proposed in both views. This is a reference correction, not improved predictions or new inference. Original [readiness tables](readiness-2026-10-08.md) and both numeric views are preserved. Qwen3.5 role-first also has the 1.28-second difference under original labels and zero under corrected labels.

## Completed and stopped comparisons

Criteria were recorded before fresh semantic inspection. Qwen3.8 failed known5; its fresh run finished for diagnosis, while its 33-case and eight-case synthetic suites were **not run**. The already frozen Qwen3.5 role-first prompts passed four consumed diagnostic requests and known5, but failed the completed fresh comparison. Its two synthetic suites were also **not run**. Skipped cases are neither passes nor execution failures. No fresh outcome was retuned or relabeled into a pass.

The original v8 synthetic results remain [historical evidence](readiness-2026-10-08.md). Both authored sets are consumed regressions. Kev completed them: 4B proposed all ad seconds plus 71.8 and 6.0 editorial seconds; original 9B missed five ad seconds in the 33-case set and proposed 54.0 and 20.5 editorial seconds. Quoted targets improved recording coverage but added editorial speech. Wider context worsened recording results.

Matched 4B and 9B controls used identical 98 payloads and current sentence assembly, comparing checkpoint packages rather than parameter count alone. Historical 4B used earlier assembly. Its runtime snapshot was captured after the run and cannot retroactively guard omitted configuration files; the setup helper now pins all required loader files.

Kev27 produced no complete quality comparison here. Native BF16 CPU loading took 37.91 seconds and reached 49.75 GiB resident memory, but one request exceeded 180 seconds without a result. The first GPU NF4 attempt made 15 HTTP calls, including two timeouts, then blocked 44 attempts before HTTP. No complete clip succeeded. The capped attempt made 39 calls: 37 succeeded, one hit an allocator OOM and one was aborted during cleanup. Two clips completed; three clips and all synthetic cases failed. Sampled physical free memory did not measure allocator-cap headroom. Separate [first](evaluations/2026-10-08-kev27-nf4-failure.json) and [capped](evaluations/2026-10-08-kev27-nf4-cap75-failure.json) failure records retain actual call counts and eligible populations. These failures do not establish accuracy or rule out other runtimes.

## Runtime and retained application

These are actual requests on a shared RTX 3090 Ti workstation. Wrapper time includes guards and overhead; download, ASR, model startup and export are separate.

| Run | Requests | Summed HTTP seconds | Wrapper seconds |
| --- | ---: | ---: | ---: |
| Qwen3.5 role-first, known5 | 60 | 3432.971 | 3501.343 |
| Qwen3.8 role-first, known5 | 60 | 3636.264 | 3706.984 |
| Original v8, fresh3 | 20 | 1105.169 | 1142.906 |
| Qwen3.5 role-first, fresh3 | 20 | 1094.528 | 1132.828 |
| Qwen3.8 role-first, fresh3 | 20 | 1176.481 | 1217.047 |

Retained v8 needed about **18.42 minutes of HTTP request time for 13 minutes of excerpts**. Historical known-v8 results used exact selection replay of saved v7 responses, not a newly timed arm. Independent audits reproduced every completed Qwen request and saved outcome without inference. An isolated Qwen3.5 production proposal also matched its 80 requests and eight cut lists except the policy label, but was **not applied** after rejection. No Qwen3.8 profile UI or prompt change shipped.

The application remains on `intent-boundary-v8`, with 122 sample suggestions, zero approved cuts and prior cut history preserved. Main-code checks passed **496 tests plus 121 subtests**, with one existing Starlette/httpx warning. Optional Kev 4B/9B artifact verification passed offline without changing saved provenance.

These labels use provisional ASR timestamps and publisher context, not listening-confirmed acoustic truth. Missing speech, music and silence are outside recognized word coverage. No result establishes that every ad will be removed or all editorial speech preserved. Keep review enabled, listen, and select the cuts yourself.
