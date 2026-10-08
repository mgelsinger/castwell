# Castwell readiness, October 8, 2026

**Use Castwell with manual review. The current v8 candidate does not establish reliable unattended ad removal.** Its dense Qwen3.5-27B proposals cover 408.96 of 410.24 annotated commercial word-seconds across five known excerpts, miss 1.28, and also select 11.68 protected word-seconds. The full 46.48-second rutabaga parody is preserved. All cuts remain unapproved until selected by the user.

These are provisional ASR-based labels and timestamps, without human listening verification. Word-time excludes missing ASR and silence between words; it is not complete acoustic ad recall. The approximately fifty-minute convenience sample includes four shows and two excerpts from one episode. It has been inspected during development and is not a fresh holdout.

## What was evaluated

The [v8 freeze](evaluations/2026-10-08-qwen35-dense-v8-candidate.json), SHA256 `6ce89438bd523166d0f661b11886cdeec27ef6a5bc6c2312c4a03638eba310ec`, changes one v7 selection rule: agreement that a unit is editorial suppresses its proposal regardless of confidence. Explicit uncertainty, mixed judgments and disagreement still produce review suggestions. Commercial verification retains its threshold and alignment safeguards.

| Evidence | Execution and status |
| --- | --- |
| [Dense v7 recordings](evaluations/2026-10-08-real-qwen35-dense-v7.json) | Actual local inference: five successes, zero failures, 60 captured requests. |
| [Dense v8 recordings](evaluations/2026-10-08-real-qwen35-dense-v8.json) | Exact HTTP response replay: five successes, all 60 URL/payload pairs matched, all records consumed, zero new inference. Model, prompts, profile and input identities match v7. |
| [V8 eight-case source-credit challenge](evaluations/2026-10-08-source-credit8-qwen35-dense-v8.json) | First frozen actual inference: eight successes, zero failures, 16 requests. Its results have now been inspected. |
| [V8 33-case regression](evaluations/2026-10-08-regression33-qwen35-dense-v8.json) | Actual local inference: 33 successes, zero failures, 66 requests. Previously inspected development material. |

The replay reruns the ordinary detector and evaluator against every saved response, including omitted editorial negatives. It rejects request, transport or input mismatches and has no network fallback. This isolates the changed selection rule; it is not an independent model run. The report retains v7 as the original inference identity and records the v8 selection policy separately. See the [numeric comparison](evaluations/2026-10-08-real-word-time-coverage-dense-v8.json) and [reproduction instructions](local-models.md#qwen35-27b-dense-v8-candidate).

## Results on the same five excerpts

Candidates are every proposed cut. Verified suggestions pass both correlated model checks and alignment safeguards. Approved cuts are actual application selections; they are empty because review was enabled. A verified suggestion is not automatically selected in the app.

| Measured view | V8 candidates | V8 verified | V7 candidates | Earlier v6 candidates | Kev v2 candidates |
| --- | ---: | ---: | ---: | ---: | ---: |
| Commercial word-seconds selected / 410.24 | 408.96 | 280.10 | 410.24 | 355.60 | 371.36 |
| Commercial word-seconds missed | 1.28 | 130.14 | 0 | 54.64 | 38.88 |
| Protected word-seconds selected | 11.68 | 0 | 55.20 | 155.80 | 60.54 |
| Commercial continuous seconds selected / 467.44 | 425.68 | 292.00 | 426.96 | 366.82 | 384.66 |
| Protected continuous seconds selected | 12.16 | 0 | 58.02 | 158.12 | 65.22 |

V8 removes 43.52 protected word-seconds from v7 proposals, but also loses 1.28 commercial word-seconds. Its verified subset is unchanged: zero selected protected word-time in this sample comes with 130.14 commercial word-seconds left outside that subset. These results do not justify selecting only verified suggestions and assuming all ads are removed, or selecting every proposal and assuming editorial speech is safe. Kev has no two-pass verified view; it is a separate local model, not official Jev.

| V8 excerpt | Candidate commercial word-time | Candidate protected word-time | Verified commercial word-time |
| --- | ---: | ---: | ---: |
| Stuff They Don't Want You To Know | 117.34 / 117.34 | 1.00 | 79.76 |
| Stuff You Should Know | 124.82 / 124.82 | 0 | 76.48 |
| Skeptoid | 132.38 / 133.66 | 10.68 | 105.20 |
| Diary Of A CEO, opening | 15.80 / 15.80 | 0 | 5.74 |
| Diary Of A CEO, closing | 18.62 / 18.62 | 0 | 12.92 |

The complete rutabaga reference interval and surrounding editorial discussion are outside v8 proposals. One second of the network identity remains a false proposal. Skeptoid contributes the remaining 10.68 protected word-seconds and the 1.28 commercial word-seconds missed from a paid-event promotion. All thirteen commercial units are touched, but none is entirely selected in continuous time: 41.76 annotated commercial seconds remain outside candidates, mostly between aligned words and segments. Listening and boundary review remain necessary.

The actual dense inference took **3,541.438 seconds, or 59.02 minutes**, for these approximately fifty minutes of excerpts on an RTX 3090 Ti. The offline v8 replay took **23.953 seconds** and made no model calls; this is replay execution time, not a classifier speedup. Inference time excludes downloads, ASR, model startup and rendering. Kev took 116.64 seconds and v6 took 859.297, with the different measured errors above.

## Narrow synthetic check

The eight-case source-credit challenge contains **34 authored commercial seconds and 180 editorial seconds**. Proposals cover all commercial speech but include 23.5 editorial seconds: 16 in a sponsored-reporting case and 7.5 in a source-credit introduction. Both remain review suggestions. Proposals match six of eight cases exactly. Simulated approved and threshold-shadow intervals match all eight cases, with no missed ads or selected editorial seconds and zero matched-boundary error.

This was actual local inference under frozen v8, not replay. It took about 8.67 minutes. Approval in this synthetic experiment is simulated with review disabled; actual application and recording-run approvals remain empty. The set has no ambiguous cases or positive expected-review cases, so it provides no uncertainty-detection result. Eight authored examples focused on source credits, offers and parody do not establish broad accuracy. After this first run was inspected, the set became consumed material for any later tuning.

## Consumed synthetic regression

The 33-case run completed successfully under the same frozen v8 candidate, taking about 37.65 minutes. Its thirty binary cases contain 306.8 authored commercial seconds and 501.4 editorial seconds. Proposals cover every commercial interval plus **49 editorial seconds**: 39 inside three coarse mixed segments and ten beside two paid self-promotions. Proposals match 25/30 binary cases exactly.

Simulated approved and threshold-shadow intervals select 272.8 commercial seconds, miss **34** inside the three coarse mixed segments, and select zero editorial seconds, matching 27/30 cases exactly. All six expected-review cases are flagged, along with two extra self-promotion cases. All three ambiguous cases produce review suggestions totaling 65 seconds and zero simulated approvals; their 75 seconds are excluded from accuracy denominators. Paid humor, parody and source quotations behave correctly in these known examples, but these repeated fictional cases do not establish real-host generalization.

## Reference scope and remaining limits

| Publisher source | Fixed excerpt |
| --- | --- |
| [Stuff They Don't Want You To Know](https://omny.fm/shows/stuff-they-dont-want-you-to-know/listener-mail-sleeper-agents-devil-corp-automated-book-scanners-and-the-gandalf-protest) | August 27, 2026; first ten minutes including the rutabaga sketch. |
| [Stuff You Should Know](https://omny.fm/shows/stuff-you-should-know-1/short-stuff-the-style-cycle) | October 7, 2026; first ten minutes. |
| [Skeptoid](https://skeptoid.com/episodes/1061) | October 6, 2026; first ten minutes. |
| [The Diary Of A CEO](https://rss2.flightcast.com/xmsftuzjjykcmqwolaqn6mdn) | September 25, 2026; opening and closing excerpts of the Nicola Kilner replay. |

References were annotated from publisher context and local `small.en` transcripts before initial predictions, then fixed as v3 with provisional gap recovery. Paid products/events count as commercial; free show teasers and editorial credits are protected. Unresolved ranges are excluded, and unlabeled audio is not presumed editorial. Source hashes and clip offsets distinguish recordings whose dynamically inserted ads may differ on another download. Audio, full transcripts and model evidence remain private; published reports contain metadata and numbers.

The independent audit reconstructs interval unions, exclusions, each unit/view and aggregates, then checks input identities and public/private correspondence. Word-time comparisons use the same transcript, reference, source-audio, clip and offset hashes. They do not establish generalization to unfamiliar episodes, hosts, accents or non-speech advertisements. Two judgments from one model are correlated, and its scores are uncalibrated self-reports.

Word-aligned transcripts allow sentence boundaries; coarse mixed segments cannot locate internal cuts and require manual review. Gap recovery may recover missed words, but marks them provisional and blocks automatic approval. Gaps can also be silence or music. No human listening verification of these labels or acoustic boundaries is claimed. No paid API calls were made.

## Preserved comparisons

The [v4 recording report](evaluations/2026-10-08-real-qwen35-v4.json) succeeded on four excerpts and failed on Skeptoid after bounded structured-output retries. Its full eligible denominator remains 410.24 commercial word-seconds; the failed excerpt contributes 133.66 eligible but unscored seconds. On the same four successful excerpts, v8 candidates select 276.58/276.58 commercial word-seconds and 1.00 protected word-second, versus v4's 263.24 and 19.96. The failed clip is excluded from both sides of this pair, not reclassified as correct.

The [v6 report](evaluations/2026-10-08-real-qwen35-v6.json) and [v6 word-time comparison](evaluations/2026-10-08-real-word-time-coverage-v6.json) preserve its substantial false proposals, mistaken verified source credit and missed Skeptoid promotion. The [Kev report](evaluations/2026-10-08-real-kev-v2.json) preserves its separate typed-decision results, including 33.50 seconds proposed inside the rutabaga parody.

The original 27 synthetic cases are development material. The subsequent 33 cases are also consumed regressions: thirty binary cases contain 306.8 commercial and 501.4 editorial seconds, while three ambiguous cases totaling 75 seconds are excluded from accuracy denominators. Empty ambiguous references do not establish a definitive editorial label. The [initial Qwen v4 holdout](evaluations/2026-10-08-fresh-holdout-qwen-v4.json), [Kev companion](evaluations/2026-10-08-fresh-holdout-kev-v2.json), and [Qwen3.5 v4](evaluations/2026-10-08-regression33-qwen35-v4.json), [v5](evaluations/2026-10-08-regression33-qwen35-v5.json) and [v6](evaluations/2026-10-08-regression33-qwen35-v6.json) results remain unchanged. V5 was rejected on synthetic errors and never attempted recordings. The [October 4 archive](evaluations/README.md) records the earlier Qwen2.5 comparison. See the [evaluation guide](ad-read-evaluation.md) for fixture hashes and methodology.

## Application and export checks

V8 passed **465 tests and 121 subtests** in 27.21 seconds. The guarded sample import updated all five local clips with 122 unapproved proposals; reference/audio bytes, settings and listening state were unchanged, and prior cut history was preserved. Both database and app API matched the v8 replay provenance. A repeat preview recognized all five as already imported. This did not invoke inference or render audio. The v8 browser smoke test passed in 26.8 seconds, including exact boundary/metadata preservation behind display-only number formatting and manual-edit safeguards. Read-only real-sample checks passed on desktop and 390-pixel mobile layouts without database changes. The final 121,862-byte wheel passed isolated import and CLI-help checks; all 17 packaged source/assets and README metadata match the checkout, with no private runtime files. Its exact hash is in the validation record. The [Checks workflow](https://github.com/mgelsinger/castwell/actions/workflows/ci.yml) runs Python 3.10/3.12 tests, JavaScript syntax checks and Chromium desktop/mobile smoke tests; inspect the run matching the exact Git revision. Details are in the [validation record](validation.md).

The editor defaults to manual approval, separates verification from selection, retains review metadata and invalidates selection when boundaries change. Transcript regeneration preserves reviewed cuts on unchanged audio. A separate [export mechanics check](evaluations/2026-10-08-export-mechanics.json) removed a reference-selected 19.82 seconds from a 597.100437-second clip: retained PCM matched exactly, all 1,737 retained words mapped, and each SRT/VTT contained 108 valid cues. These checks validate editing mechanics, not automatic detection or listening quality. The README media uses a synthetic demo and makes no accuracy claim.
