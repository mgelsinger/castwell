# CUDA classifier speed repair, October 12, 2026

**The released 0.5.1 classifier is too slow for the requested batch workflow. The experimental V4 candidate is substantially faster, but still fails the requirement to avoid cutting program speech. A subsequent V5 adjustment regressed accuracy and was rejected.** V4 remains an unrouted source prototype. The default policy, published installer, and portable app are unchanged.

On the same 79:55 recording, fresh V4 classification took **6:06.920**, compared with **106:34.172** for the released classifier. It proposed all 2,098 reference-labeled commercial words, preserved the rutabaga parody, and incorrectly proposed seven protected words immediately before the first advertisement. Every proposal remained unapproved. No audio was automatically removed and no paid API calls were made.

## Full recording comparison

This is a workflow comparison using the same Qwen3.8 27B Q4_K_M weights and native CUDA runtime, not a comparison between different models.

| Measure | Released intent/boundary v8 | Experimental scene/boundary V4 |
| --- | ---: | ---: |
| Fresh classification wall time | 6,394.172 seconds | 366.919842 seconds |
| Model requests, including retries | 99 | 36 |
| Generated completion tokens | 265,448 | 11,510 |
| Transcript review units | 1,137 | 1,137 |
| Commercial words missed | 42 / 2,098 | 0 / 2,098 |
| Commercial aligned word-seconds missed | 13.02 / 626.06 | 0 / 626.06 |
| Protected words proposed for cutting | 17 / 10,862 | 7 / 10,862 |
| Protected aligned word-seconds proposed | 2.58 / 3,359.57 | 2.56 / 3,359.57 |
| Rutabaga and two other parody passages | Preserved | Preserved |
| Automatically approved cuts | 0 | 0 |
| Acceptance result | Failed: misses and extra cuts | Failed: extra cuts |

V4 used **94.26% less classification time**, a **17.43-fold speed improvement** on this recording. The fresh run had no response-cache hits. It covered every transcript review unit and included all validation retries in its measured time. Model startup, transcription, downloading, scoring, and audio export are excluded from classification time. The reused transcript originally took 130.06 seconds to produce on CUDA; transcription was not rerun for this comparison.

The remaining V4 false proposal is podcast/network identification directly before the first commercial read. Its original-audio aligned intervals are 233.05-235.11 and 236.97-237.47 seconds, totaling 2.56 aligned word-seconds across seven words. The earlier sponsor transition and both humorous commercial introductions were handled correctly in this run. A passing seven-passage pilot did not predict this full-recording error.

## What changed, and its tradeoffs

| Setup | Benefits | Costs and limitations |
| --- | --- | --- |
| Released verbose two-pass review | Separate intent and boundary judgments for every unit; detailed per-unit evidence | Generated 265,448 completion tokens across 99 requests. About 97% of measured native model processing time was output generation. On this recording it still missed two commercial introductions and proposed a sponsor transition. |
| Compact scene sweep plus focused boundary review | The first sweep reviews every unit without keyword gating. Compact ordered ranges reduce output. A second review targets proposed commercial scenes and their neighbors, with a small reasoning budget. Original word timing supplies all cut boundaries. | Focused review depends on the first sweep identifying the scene. Mixed or invalid ranges need strict validation and bounded retries. V4 recovered all labeled commercial words but still proposed seven protected words. Negative units without a second judgment are explicitly marked as unaudited by the boundary stage. |
| Boundary editorial veto alone, V2 diagnostic | Prevented the V1 protected-word proposals without additional inference | Missed 81 words of a humorous commercial setup. A conservative combination rule alone did not solve scene recognition. |

The compact policy requires ordered, complete coverage of requested IDs. A range marked mixed must contain exactly one unit and use that unit as its evidence. The first sweep uses no model thinking; focused boundary requests allow 256 reasoning tokens. Both stages have a 2,048-token output limit. All cuts are suggestions requiring explicit review, including when the global review preference is disabled.

## Small, standardized development diagnostics

The same seven diagnostic passages were retained through all four candidates: the rutabaga sketch, a sponsor transition with real reads, a mock show slogan, an ordinary scanner product discussion, both complete humorous Underdog reads, and a puppy-stock parody. Whole fixed excerpts were scored, including surrounding editorial speech. These are consumed development examples, not unseen tests.

| Candidate | Execution and timing basis | Commercial words missed | Protected words proposed | Diagnostic gate |
| --- | --- | ---: | ---: | --- |
| V1, compact non-thinking stages | 14 fresh requests, 123.084286 seconds | 0 | 20, totaling 3.32 word-seconds | Failed |
| V2, boundary editorial veto | 14 exact recorded responses replayed; 0 new requests; 0.242334 seconds of offline replay | 81, totaling 24.18 word-seconds | 0 | Failed |
| V3, bounded boundary thinking and scene instructions | 9 exact first-stage responses reused; 3 fresh boundary requests taking 53.874469 seconds | 0 | 16, totaling 3.84 word-seconds | Failed |
| V4, singleton mixed-range validation | 9 exact first-stage responses reused; 3 fresh boundary requests taking 49.351661 seconds | 0 | 0 | Passed |

V3 and V4 reuse first-stage predictions whose historical native model time was 67.14 seconds. Their reported new boundary times are **not** fresh complete-pipeline times. V2's replay time is **not** inference performance. Only the fresh full-recording V4 run supports the 6:06.920 full classification figure. The diagnostic gate required complete decisions, no missed or partially selected commercial words, no proposed protected words anywhere in the fixed excerpts, and manual approval preserved.

V1 covered the real reads but included two editorial units. V2 removed those proposals and lost part of a real commercial setup. V3 recovered that setup but grouped a return to the show into a mixed advertising range. V4 enforced single-unit mixed ranges and passed the diagnostic passages. The subsequent full-recording failure remains part of this evaluation.

## Rejected V5 follow-up

V5 added a general boundary instruction distinguishing publisher/network identification from commercial content. It reused **30 exact full-recording first-stage responses** from V4 and made **eight fresh boundary requests**, including retries. The new boundary requests took **115.424334 seconds**. The reused intent requests had historical native model time of **271.472 seconds**. These are separate timing categories; V5 was not a fresh complete-workflow speed benchmark.

| V5 staged full-recording outcome | Result |
| --- | ---: |
| Review units completed | 1,137 |
| Commercial words missed | 44 / 2,098 |
| Commercial aligned word-seconds missed | 13.08 / 626.06 |
| Protected words proposed | 42 / 10,862 |
| Protected aligned word-seconds proposed | 10.36 / 3,359.57 |
| Automatically approved cuts | 0 |
| Acceptance result | Failed |

The adjustment preserved the publisher identification that V4 had proposed, but missed part of the first Underdog read and proposed editorial speech before a later commercial block. It was rejected. The source was restored to the exact V4 prototype, and no fresh full V5 confirmation, application routing, default change, or installer update was performed. This regression shows why a targeted prompt correction still requires comparison against the complete fixed reference.

## Recording, runtime, and limits

- Show: **Stuff They Don't Want You To Know**. Episode: **Listener Mail: Sleeper Agents, Devil Corp, Automated Book Scanners, and the Gandalf Protest!**, published August 27, 2026. [Publisher episode page](https://omny.fm/shows/stuff-they-dont-want-you-to-know/listener-mail-sleeper-agents-devil-corp-automated-book-scanners-and-the-gandalf-protest).
- One fixed cached recording, duration 4,795.010612 seconds. Dynamic advertisements may differ in later downloads.
- Hardware: NVIDIA GeForce RTX 3090 Ti, with **66 of 66 model layers on CUDA**. The candidate made no CPU inference fallback.
- Same frozen transcript and reference: 13,019 aligned word entries, 1,137 review units, 14 commercial reads, 10,862 protected words, and 59 ambiguous trailer words excluded from binary accuracy scoring. All ambiguous words remained unselected.
- The reference was frozen before the original baseline predictions. It has now been inspected and used for development, so the current recording is **not held out**.
- The reference assesses commercial intent from the transcript and publisher context. **Human listening and payment status have not been verified.** Zero scored misses would not establish zero audible misses or flawless boundaries.
- Nine unverified transcription gaps total **66.68 seconds**. V4 selected none of them. They remain unknown audio, not confirmed silence or noncommercial content.
- V4 is an unrouted source candidate evaluated through the classifier harness. It has not received a complete installed-app/export acceptance test or an installer update. V5 was rejected after its staged full-recording regression.

The new workflow resolves most of the measured generation bottleneck. Production acceptance still requires the extra-cut error to be fixed, fresh independently reviewed recordings, listening around boundaries and unknown gaps, and an installed-app/export test. No claim of unattended, error-free ad removal is supported by these results.

Exact public hashes, numeric outcomes, and timing categories are in the [companion JSON](2026-10-12-classifier-speed-repair.json). The [original full CUDA evaluation](2026-10-11-full-stdwytk-cuda.html) remains unchanged. Audio, transcript excerpts, model evidence text, private request caches, API credentials, and workstation paths are intentionally absent from these public artifacts.
