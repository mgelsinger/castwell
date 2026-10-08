# Castwell readiness checkpoint, October 8, 2026

**The evaluated v6 candidate is inadequate for unattended ad removal.** It completed all five recording excerpts, preserved the rutabaga parody and recovered two previously missed commercial introductions. It also proposed substantial editorial speech, mistakenly verified an editorial source credit as commercial, and missed most of a paid-event promotion. Use Castwell as a review-assisted editor. All recording-run cuts remained unapproved because review was enabled; that is a policy setting, not an accuracy result.

## Completed v6 recording regression

The [candidate freeze](evaluations/2026-10-08-qwen35-v6-candidate.json), [recording results](evaluations/2026-10-08-real-qwen35-v6.json) and [independent word-time comparison](evaluations/2026-10-08-real-word-time-coverage-v6.json) identify the model, code, recordings and reference hashes. These are **known-recording regression results**, after earlier outputs were inspected. The source and references remained fixed throughout the five-clip run.

Both v6 and [local Kev](evaluations/2026-10-08-real-kev-v2.json) successfully scored the same five excerpts. Their references contain 467.44 commercial continuous seconds, 410.24 commercial word-seconds, 2,370.47 protected continuous seconds and 1,995.07 protected word-seconds.

| Same five recordings | V6 candidates | V6 verified subset | Kev candidates |
| --- | ---: | ---: | ---: |
| Commercial continuous seconds selected | 366.82 / 467.44 | 232.16 / 467.44 | 384.66 / 467.44 |
| Commercial continuous seconds missed | 100.62 | 235.28 | 82.78 |
| Commercial word-seconds selected | 355.60 / 410.24 | 224.74 / 410.24 | 371.36 / 410.24 |
| Commercial word-seconds missed | 54.64 | 185.50 | 38.88 |
| Protected continuous seconds selected | 158.12 | 6.20 | 65.22 |
| Protected word-seconds selected | 155.80 | 6.20 | 60.54 |

Word-time counts the union of provisional ASR word intervals inside eligible reference spans. It distinguishes recognized speech from timestamp gaps, but cannot measure speech absent from ASR or establish acoustic truth. Continuous-span and per-unit metrics remain available separately. Kev additionally selected 6.74 unlabeled seconds; these are not presumed editorial. Neither candidate selected unresolved reference ranges.

**Candidates, verified suggestions and actual approvals are different.** V6's verified view requires two model judgments to agree plus alignment safeguards. Those judgments come from the same model and can share the same mistake. Kev has no two-pass verifier, so that view is unavailable rather than zero. Actual approvals were empty for both runs.

### Results by excerpt

| V6 excerpt | Candidate commercial word-seconds / annotated | Protected continuous seconds proposed | Protected seconds verified |
| --- | ---: | ---: | ---: |
| Stuff They Don't Want You To Know | 117.34 / 117.34 | 0 | 0 |
| Stuff You Should Know | 124.82 / 124.82 | 141.06 | 6.20 |
| Skeptoid | 79.02 / 133.66 | 14.84 | 0 |
| Diary Of A CEO, opening | 15.80 / 15.80 | 0 | 0 |
| Diary Of A CEO, closing | 18.62 / 18.62 | 2.22 | 0 |

- **Rutabaga parody:** v6 kept the full 46.48-second reference interval and surrounding protected material. All annotated commercial words in the four neighboring ads were proposed, including the previously missed American Military University introduction. Its 11.34 missed continuous commercial seconds are between transcript segments. The verified subset covers only 65.06 commercial word-seconds; the entire CBS/Paramount Plus commercial remains a disputed review suggestion. Kev proposed 33.50 seconds of the parody, nine seconds of surrounding discussion and 1.60 seconds of network identity.
- **Stuff You Should Know:** v6 proposed 141.06 protected continuous seconds, including 139.22 word-seconds. At 80.55-86.75, both checks incorrectly treated an editorial source acknowledgement as sponsorship, each reporting confidence 0.95. The subsequent fashion discussion contributes the other protected proposals. V4 proposed none of this content. All commercial word intervals are suggested, but only 76.48 word-seconds pass verification. Recovered Capital One speech correctly remains outside that subset.
- **Skeptoid:** v6 returned valid results but missed 54.64 commercial word-seconds from the opening paid-event promotion, selecting only 5.72 of its 60.36 word-seconds. Odoo and the closing event promotion have complete candidate word coverage. Protected introductory material contributes 14.84 continuous seconds of false proposals, none verified. Kev covered more commercial words on this excerpt. Returning valid output did not establish correct detection.
- **Diary Of A CEO, opening:** v6 candidates recover the full 15.80 commercial word-seconds; v4 and Kev selected only 5.74. The lead-in still fails verification, leaving the verified subset at 5.74. The remaining 0.18 continuous seconds are gaps between aligned words.
- **Diary Of A CEO, closing:** all 18.62 annotated commercial word-seconds are proposed, along with 2.22 protected editorial seconds. Those protected suggestions remain unverified; the verified commercial subset covers 12.92 word-seconds.

All thirteen annotated commercial units are touched by v6 candidates, but none is completely covered in continuous time. Timestamp gaps account for some of that incompleteness; the Skeptoid omission also includes substantial recognized commercial speech. The complete run has **five successes, zero failures and none pending**, taking 859.297 classifier seconds. Kev took 116.64 seconds. These timings exclude download, ASR, model startup and rendering.

## Comparison with the preserved v4 run

The [v4 report](evaluations/2026-10-08-real-qwen35-v4.json) completed four clips and failed structured-response validation on Skeptoid after 198.375 seconds. It retains all five clips' eligible denominators: 467.44 commercial continuous seconds and 410.24 commercial word-seconds. Its scored subset contains only 311.58 and 276.58, respectively. The failed Skeptoid clip contributes 155.86 continuous and 133.66 word-seconds that remain eligible and unscored. V6's later success does not replace that failure.

The following comparison uses **only the same four successful clips across all three runs**. It excludes Skeptoid from each column and preserves the v4 failure separately.

| Candidates on the same four clips | Qwen3.5 v4 | Qwen3.5 v6 | Kev v2 |
| --- | ---: | ---: | ---: |
| Commercial continuous seconds selected / 311.58 | 271.72 | 285.88 | 266.22 |
| Commercial word-seconds selected / 276.58 | 263.24 | 276.58 | 257.78 |
| Commercial word-seconds missed | 13.34 | 0 | 18.80 |
| Protected continuous seconds selected | 19.96 | 143.28 | 54.06 |
| Protected word-seconds selected | 19.96 | 141.44 | 49.86 |

V6 recovers 13.34 commercial word-seconds but adds 121.48 protected word-seconds on this matched subset. Its verified view also covers slightly fewer commercial word-seconds, 160.20 versus v4's 164.92, while adding 6.20 protected seconds versus zero. The earlier [v4 word-time artifact](evaluations/2026-10-08-real-word-time-coverage.json) remains unchanged. V4's complete attempt took 1,456.11 seconds including its failure.

## Protocol and limits

These are approximately fifty minutes from four user-selected shows, including two excerpts from one episode:

| Publisher source | Fixed excerpt |
| --- | --- |
| [Stuff They Don't Want You To Know](https://omny.fm/shows/stuff-they-dont-want-you-to-know/listener-mail-sleeper-agents-devil-corp-automated-book-scanners-and-the-gandalf-protest) | August 27, 2026; first ten minutes including the rutabaga sketch. |
| [Stuff You Should Know](https://omny.fm/shows/stuff-you-should-know-1/short-stuff-the-style-cycle) | October 7, 2026; first ten minutes. |
| [Skeptoid](https://skeptoid.com/episodes/1061) | October 6, 2026; first ten minutes. |
| [The Diary Of A CEO](https://rss2.flightcast.com/xmsftuzjjykcmqwolaqn6mdn) | September 25, 2026; opening and closing excerpts of the Nicola Kilner replay. |

References were annotated from publisher context and local `small.en` ASR before initial predictions, then fixed as v3 with provisional gap recovery. Paid products/events count as commercial; free show teasers and editorial credits are protected. **No human listening verification of labels or boundaries is claimed.** Dynamic ads can change source audio, so download identities, offsets and hashes are recorded. Audio, transcripts and model evidence remain private; published recording reports contain metadata and numbers.

Only labeled intervals are scored. Unresolved ranges are excluded, and unlabeled audio is not assumed editorial. Independent atomic-time reconstruction checked every scored view/unit, aggregate, input hash and public/private correspondence. Failed and unfinished durations remain explicit. Word-time comparisons require identical reference, transcript, audio and offset hashes. This convenience sample cannot establish accuracy on unfamiliar hosts, accents or episodes.

The [initial candidate](evaluations/2026-10-08-candidate.json) records Qwen3-14B and Kev. The [Qwen3.5 v4 freeze](evaluations/2026-10-08-qwen35-candidate.json) preceded inspection of real outcomes. V5/v6 follow-ups used those inspected results. V6 combines source-ID evidence selection, application-attached exact text, bounded invalid-output subdivision and exact-touching sentence assembly with refined context instructions. None makes the evidence source ID an independent fact-check.

Qwen3.5-35B-A3B ran with thinking disabled, using Q4_K_M locally requantized from pinned Q8_0 weights and four expert layers on CPU. Kev is a separate local model, not official Jev. No paid API calls were made. Later decoding or policy experiments need their own frozen records and cannot be combined with these v6 numbers.

## Synthetic regressions and rejected candidates

The original 27 cases are development material. The [33-case fixture](../tests/fixtures/ad_read_holdout_v2.json) was initially frozen, but all later reruns are consumed-set regressions:

```text
SHA256 4581c6c745d33aceaa3dd4e238dc6358c3daf6a1e9ea88c80fa5de1fbabdf051
```

Thirty binary cases contain 306.8 authored commercial seconds and 501.4 editorial seconds. Three ambiguous cases totaling 75 seconds are excluded from those accuracy denominators. Empty ambiguous references mean unsupported automatic removal, not a definitive editorial label.

| Synthetic run | Candidate ad seconds missed | Candidate editorial seconds selected | Simulated approved editorial seconds | Unsupported ambiguous approvals |
| --- | ---: | ---: | ---: | ---: |
| [Qwen3-14B v4](evaluations/2026-10-08-fresh-holdout-qwen-v4.json) | 3.2 | 89 | 25 | 0 |
| [Kev v2](evaluations/2026-10-08-fresh-holdout-kev-v2.json) | 0 | 71.8 | Not applicable | 0, mandatory review |
| [Qwen3.5 v4](evaluations/2026-10-08-regression33-qwen35-v4.json) | 0 | 59 | 0 | 0 |
| [Qwen3.5 v5](evaluations/2026-10-08-regression33-qwen35-v5.json) | 0 | 81.6 | 5 | 10 |
| [Qwen3.5 v6](evaluations/2026-10-08-regression33-qwen35-v6.json) | 0 | 59 | 0 | 0 |

V6's 59 extra editorial seconds are 39 in coarse mixed segments, ten in quoted advertising and ten beside paid self-promotion. Its 34 unapproved commercial seconds are inside the three coarse mixed units, whose internal boundaries are unavailable without word timestamps. Approved intervals match 27 of 30 binary cases. All three ambiguous cases signal review, with 65 proposed seconds and none approved. Paid-humor and unpaid-parody distinctions match these known examples; the real-recording mistakes show why that is insufficient.

The [v5 candidate](evaluations/2026-10-08-qwen35-v5-candidate.json) was rejected after five simulated approved editorial seconds in a program-navigation introduction and ten unsupported approved seconds in an ambiguous relationship case. That case also had a review flag elsewhere: review presence does not make its other approvals safe. **V5 never attempted real recordings**; no real-recording failure or coverage denominator exists for it.

Each listed synthetic run returned 33 valid results. V6 took 273.00 classifier seconds. Duration, boundary and ambiguity metrics were independently reconstructed. The [Kev development results](evaluations/2026-10-08-kev-development-v2.json) and [October 4 Qwen 2.5 archive](evaluations/README.md) remain available. Authored timestamps and fictional host groups cannot establish real-podcast accuracy.

## Editor and export checks

- New settings require approval for every cut; existing preferences remain respected. Both-checks-passed status is distinct from actual selection.
- Word timestamps permit sentence-level boundaries. Coarse mixed segments require review; no internal timestamps are invented. Gap recovery preserves original speech, marks provisional words and blocks their automatic approval.
- Regenerating text preserves reviewed cuts on unchanged audio. Retry retains completed stages. Fresh playback starts at 1x; editing cleaned audio preserves the equivalent original playhead.
- Saves retain detector/recovery metadata. Editing boundaries clears selection, retains original detected bounds and marks prior evidence as applying only to those original times.

Browser regressions cover playback, review selection, metadata and exports. Mocked evaluator tests cover exclusions, failures, local-only calls and redaction. A separate [export mechanics check](evaluations/2026-10-08-export-mechanics.json) removed a reference-selected 19.82 seconds from a 597.100437-second clip: retained PCM matched exactly, all 1,737 retained words mapped correctly, and each SRT/VTT had 108 valid cues. Source hashes stayed unchanged. These validate software behavior, not automatic detection or listening quality. Commands and reference formats are in the [evaluation guide](ad-read-evaluation.md).
