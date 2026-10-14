# Five-minute CUDA evaluation, October 13, 2026

**The measured speed target is achievable on the tested GPU. Unattended, error-free ad removal is not established.** The final candidate processed a fresh 75:13 recording in **315.16 seconds**, equivalent to **251.40 seconds per audio hour**, including fresh transcription, classification, waveform/database preparation, and a rendered preview. It left **1.31 commercial word-seconds** outside its cuts and selected **zero protected word-seconds** against the consumed provisional reference. Thirteen commercial word entries were partly removed and one was entirely missed.

The current scope removes sponsor/product reads, including humorous persuasive setups and paid subscriptions. It preserves free podcast promos, neutral publisher credits, program navigation, editorial product discussion, and reference-labeled parody. All proposed cuts require review. The separate benchmark preview does not approve application exports.

The final July v6 stopwatch recorded **69.80 seconds for transcription**, **205.76 seconds for detection**, **30.66 seconds for rendering**, and **8.84 seconds for episode preparation**. It made two fresh classifier requests, totaling 199.80 seconds of request time. Both speech recognition and all 66 classifier layers were verified on CUDA, with no transcript or prediction reuse. The complete measured wall time, rather than a sum of earlier probes, establishes this speed result.

## Measurement contract

Both recordings are fixed copies of *Stuff They Don't Want You To Know*: July 30, 2026, **4,513.123265 seconds**, and August 27, 2026, **4,795.010612 seconds**. Dynamically inserted ads may differ in another download. References use the clarified sponsor/product-only policy: July contains 1,985 commercial and 10,512 protected word entries; August contains 2,098 commercial and 10,921 protected entries. Free podcast trailers previously treated as ambiguous are protected in these policy overlays.

These recordings and references have been consumed during development. Labels and timestamps are provisional ASR/context evidence, with **no independent human listening or payment-status verification**. Preserving a reference-labeled rutabaga sketch does not independently establish that a performance was unpaid. The reserved October 8 holdout was not opened or evaluated in this work.

“Missed word-seconds” means the sum of reference commercial word duration outside the proposed cut intervals. It is not total ad elapsed time. A partly intersected word is still an incomplete removal. Zero-duration ASR entries are counted separately; touching such a point does not measure audible speech. Different fresh ASR alignments can disagree with the older reference boundaries.

Cold runs use fresh speech recognition and fresh classifier predictions, starting with empty local speech/VAD caches and a newly started classifier. Their stopwatch includes model loading, audio decoding, bounded speech recovery and coverage checks, isolated episode/database and waveform preparation, analysis persistence, and rendering. It excludes initial audio/model downloads, preflight hashing/probing, UI/queue interaction, and cleanup after output creation. OS file caches are not flushed. This measures a local processing pipeline with application preparation, not a full installed-desktop acceptance test.

The August cold run briefly overlapped approximately 43 seconds of synthetic browser testing during early transcription. That work made no model calls, but Chrome compositing and FFmpeg shared machine resources. It was not an exclusive-machine measurement. The final July v6 run completed without other GPU or heavy CPU work.

## Controlled results

| Run | Timing basis | Commercial word-seconds missed | Protected word-seconds selected | Outcome |
| --- | --- | ---: | ---: | --- |
| July compact overlapping windows | Cached transcript; 23 new requests, 104.45 seconds of inference; 141.16 seconds including preview workflow | 27.08 | 34.56 | Rejected: missed a humorous paid setup and selected reference-labeled parody/publisher speech |
| July whole-episode pilot plus boundary pilot | Cached transcript; separate global and boundary probes, 95.59 and 33.20 seconds of inference | 0 positive-duration seconds; 3 zero-duration points unselected | 0 after boundary review | Useful diagnostic; not fresh end-to-end evidence |
| **July cold v3** | **235.16 seconds total; 187.58 seconds per audio hour** | **485.60** | **0** | Speed passed; accuracy failed because the final partition omitted three early commercial blocks |
| July corrected global v4 plus boundary v4 | Reuses cold-v3 transcript; global request 166.49 seconds; separate boundary request 32.24 seconds | 1.31 across 14 incompletely selected entries | 0 | Better coverage; remaining alignment errors and no new cold timing claim |
| **August cold v5** | **320.75 seconds total; 240.81 seconds per audio hour** | **19.94** | **0** | Speed passed; a boundary decision treated part of a paid television offer as protected program material |
| August boundary v6 correction | Reuses cold-v5 transcript and global decisions; one new boundary request, 29.76 seconds | 3.13 across 15 partly selected entries | 0 | Every one of 2,098 commercial entries intersects a cut, but 15 remain incomplete; not a fresh full run |
| **July cold v6, final candidate** | **315.16 seconds total; 251.40 seconds per audio hour** | **1.31 across 14 incompletely selected entries** | **0** | Speed passed; 13 partly selected entries and one fully missed entry remain; independent accuracy acceptance is outstanding |

The whole-episode pilot and its boundary probe used an older cached alignment; the later cold and replay runs use fresh batched ASR. Their zero/nonzero timing errors therefore do not form a clean model ranking. All errors remain visible in the companion aggregate JSON.

July cold-v3 used a 3,072-token reasoning budget. The response identified early reads during generation but its final complete-looking partition omitted them after the reasoning budget was exhausted. Raising the global budget to 6,144 recovered those blocks on the exact same fresh transcript. A deterministic omission guard now rejects suspicious editorial decisions containing explicit sponsorship or transactional offer cues before boundary inference. It never converts a cue into a commercial label. An explicit sponsorship line inside fiction may therefore stop analysis for review while preserving the original recording.

The v6 boundary prompt distinguishes a subscription/television product offer from a free podcast trailer. On the fixed August replay it recovered the omitted commercial passage. This is a targeted improvement on consumed evidence, not proof that every future paid/free distinction will be correct.

## Current architecture and tradeoffs

The tested machine uses an **NVIDIA RTX 3090 Ti with 24 GB VRAM**. Speech recognition is CUDA **large-v3, float16, batch size 8**, with word timestamps, bounded serial gap recovery, and CUDA VAD with CPU provider fallback disabled. A controlled July ASR-only run took 70.60 seconds for 75:13, including model loading, recovery, and coverage checks. Full-run ASR timings are recorded separately in the JSON.

Classification uses the pinned **Qwen3.8 27B Q4_K_M** GGUF in llama.cpp with **all 66 layers on CUDA**, a 32,768-token context, and no CPU neural fallback. The current global pass allows 6,144 reasoning tokens within an 8,192-token total generation limit. Boundary review allows 1,024 reasoning tokens within a 2,048-token generation limit. The final run log identifies llama.cpp build 11146, commit `7fe450e19`. Model, source, reference, and native binary hashes are in the JSON. Native binary identities were checked after the run against the existing frozen distribution manifest.

| Setup | Advantages | Limitations and decision |
| --- | --- | --- |
| Earlier verbose per-unit review | Detailed local decisions and separate checks | Excessive generated output dominated runtime; the previous full-episode classification took about 106 minutes. Retired as the speed target architecture. |
| Compact overlapping scene/word windows | Complete source coverage with much smaller output | Cropped context can mistake parody or reject a paid setup before its offer. The July control failed both directions. |
| Whole-episode decisions plus focused word boundaries | One semantic view preserves distant framing; only scene edges and mixed/uncertain units need word review; few requests | Depends on ASR coverage, a correct global partition, adequate generation budget, and accurate word alignment. Remains a reviewed workflow. |
| Official Jev whole-scene questions | Very fast, inexpensive additional intent evidence | Six consumed cases are too small to establish accuracy; one reference-labeled parody was called commercial. No automatic hybrid promotion. |
| Additional serial acoustic endpoint refinement | Can produce new timing evidence around an existing cut | The controlled pilot worsened missed duration. Rejected and left disconnected from processing. |

Complete source IDs are validated at each stage. Native token counting reserves the full output budget. Larger recordings use bounded overlapping source windows; whole commercial scenes are never silently cropped for boundary review. Such recordings have less global context than the single-window cases and need separate evaluation. The word pass cannot invent new ads inside globally protected editorial units. Persisted audits retain source ownership, request identities, unresolved decisions, and the omission-guard result. Failed analysis retains previous decisions; missing or uncertain work cannot become a successful empty “no ads” result.

Batched decoding currently has a 600 MiB float32, 16 kHz mono waveform limit, approximately 2 hours 43 minutes. Longer inputs need a separately validated processing path; they are not silently truncated or moved to CPU inference. Episodes exceeding the classifier's 32,768-token context use overlapping windows with unresolved disagreements, which has not established the same accuracy as a complete episode in one context.

## Bounded alternatives and remaining work

The official **Jev** diagnostic made **6 requests**, using **8,429 input and 264 output tokens**, for an accounted cost of **$0.000354018**. Provider request time totaled **2.094 seconds**; the paced wrapper took **7.213 seconds**. Five cases matched the consumed reference. July parody, two humorous paid reads, a free podcast promo, and a publisher tagline matched; August parody did not. These were whole-scene intent questions, not transcription, timestamp editing, rendering, or a complete-pipeline comparison. No Jev result automatically approved a cut.

The rejected acoustic timing pilot made **19 bounded CUDA crops** in **15.77 seconds including model loading**. It aligned 16 endpoints and rejected 14. Although incompletely selected commercial entries fell from 14 to 12, missed duration increased from **1.31 to 1.56 seconds**, with zero protected reference duration selected in both cases. It was not promoted. Unmapped newly heard speech and uncertain protected-neighbor alignment remain review evidence, not permission to expand a cut.

Production acceptance still needs independently reviewed recordings, listening around proposed boundaries and uncovered speech, and installed-app/export validation. The speed results do not establish zero audible misses, zero extra cuts, or production readiness. The reserved holdout must remain untouched until the candidate and acceptance criteria are frozen.

The final verification suite reported **982 tests passed, 2 skipped, and 162 subtests passed**. The completed July v6 audit, covering **12,110 source words and 15 candidate cuts**, passed the application's strict Jobs audit validator. The full rendered MP3 preview decoded successfully. These checks verify software contracts and output decodability; they do not replace listening or a full installed-desktop acceptance test.

The [aggregate JSON](2026-10-13-five-minute-cuda.json) contains only metrics, configuration, hashes, and limitations. It excludes audio, transcript passages, model reasoning, credentials, and workstation paths. Earlier results remain available in the [October 12 evaluation](2026-10-12-ad-removal-system.md) and [classifier speed comparison](2026-10-12-classifier-speed-repair.md).

## Offline cut validation and review controls

The [offline aggregate and input hashes](2026-10-13-saved-cut-validation.json) record these checks separately from model evaluation. The reusable `scripts/audit_saved_cuts.py` helper takes a timestamped transcript, saved word decisions and cuts, original and frozen output audio, benchmark report, FFmpeg/FFprobe paths, and a new private output path; `--help` lists the arguments. An optional listening manifest measures excerpt coverage. It does not call models or change approvals, and it refuses to overwrite an existing report.

A subsequent offline audit of the same frozen July source decisions checks interval arithmetic independently of the application's detector. It confirms all 30 endpoints across 15 cuts match their declared source words, all 1,955 positive-duration words classified commercial are covered, and no source words classified editorial or uncertain intersect a cut. These are saved model labels, not independently verified truth. The separate provisional-reference result remains 1.31 commercial word-seconds outside cuts across 14 entries.

The audit identifies nine retained gaps between commercial source words, totaling **12.73 seconds**. Their audio may contain silence, music, or unrecognized speech. This finding is a listening task, not permission to expand cuts automatically. Both full audio files decode. The output matches the declared edit timeline, but the full decoded source extends **26.12 ms** beyond its declared duration. That extra tail contains nonzero float PCM values; its audible significance is unresolved. The audit reports the strict duration discrepancy instead of treating it as harmless codec padding.

A private listening packet covers all 30 endpoints, all nine retained gaps, and all previous reference discrepancy windows. Its 18 comparisons contain 36 clips totaling **337.16 seconds** of playback. Proposed clips are extracted from the actual frozen full output, not freshly rendered. Source files and decisions remain unchanged. Blank observations can be saved and exported; an isolated desktop/mobile browser check verified metadata, note persistence, export, and absence of horizontal overflow or JavaScript errors. No listening was performed by those automated checks. Most of the episode remains outside these excerpts, so even completing them would not establish whole-episode acceptance.

The application review controls now offer **Hear start / Hear end**: original audio up to three seconds either side of a boundary, at normal speed, with automatic stopping and a visible Stop button. The episode player keeps its source, position, and speed. Playhead-based marking is disabled during a preview to avoid copying the wrong position. Numeric edits stop the preview, and empty cuts are explicitly described as unverified. These changes add no model requests and do not change detection timing or accuracy scores. Current Windows candidate binaries predate these source changes and require a final rebuild before distribution.
