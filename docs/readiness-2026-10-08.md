# Castwell readiness checkpoint, October 8, 2026

**Use this checkpoint as a review-assisted editor, not unattended ad removal.** Qwen3.5 preserved the annotated rutabaga parody, but missed most of an opening sponsor read and proposed some protected speech elsewhere. Its fixed real-recording run completed four clips and failed on Skeptoid. Kev completed all five, but proposed removing much of the parody. All actual cuts remained unapproved.

## Real-recording results

The frozen reports are [Qwen3.5](evaluations/2026-10-08-real-qwen35-v4.json) and [local Kev](evaluations/2026-10-08-real-kev-v2.json). The [supplementary word-time audit](evaluations/2026-10-08-real-word-time-coverage.json) compares the **same four successfully scored clips** below. It retains the fifth clip's failure separately.

| Candidates on the same four clips | Qwen3.5 | Kev |
| --- | ---: | ---: |
| Commercial continuous seconds selected / annotated | 271.72 / 311.58 | 266.22 / 311.58 |
| Commercial continuous seconds missed | 39.86 | 45.36 |
| Commercial word-seconds selected / annotated | 263.24 / 276.58 | 257.78 / 276.58 |
| Commercial word-seconds missed | 13.34 | 18.80 |
| Protected continuous seconds proposed for removal | 19.96 | 54.06 |
| Protected word-seconds proposed for removal | 19.96 | 49.86 |

Word-time is the union of provisional ASR word intervals inside annotated commercial/protected spans, with unresolved ranges excluded. It separates recognized words from timestamp gaps. It cannot measure speech absent from the transcript or establish acoustic truth. Continuous-span and per-unit metrics are unchanged.

Qwen's **verified subset** selected 170.36 commercial continuous seconds, or 164.92 of 276.58 commercial word-seconds, with zero protected seconds selected on these four clips. That additional filtering leaves more ads for manual review. Kev has no two-pass verifier, so its verified view is unavailable. **Zero actual approved deletion is the review policy, not evidence of perfect detection.**

Across all five clips, Kev proposed 384.66 of 467.44 commercial continuous seconds and selected 65.22 protected seconds. Its word-time coverage was 371.36 of 410.24 commercial word-seconds, with 38.88 missed and 60.54 protected word-seconds selected. It also selected 6.74 unlabeled seconds, reported separately from protected content.

### What happened in each recording

| Qwen3.5 clip | Commercial word-seconds selected / annotated | Protected seconds proposed |
| --- | ---: | ---: |
| Stuff They Don't Want You To Know | 114.06 / 117.34 | 0 |
| Stuff You Should Know | 124.82 / 124.82 | 0 |
| Skeptoid | Failed; 133.66 eligible and unscored | Unscored |
| Diary Of A CEO, opening | 5.74 / 15.80 | 0 |
| Diary Of A CEO, closing | 18.62 / 18.62 | 19.96 |

- **Rutabaga parody:** Qwen kept the entire 46.48-second reference interval, surrounding discussion and network identity out of candidates and verified cuts. Kev proposed 33.50 seconds of the sketch, nine seconds of surrounding discussion and 1.60 seconds of network identity. The unpaid-parody reference was fixed before predictions.
- **Real ads around the parody:** Qwen proposed material in all four commercial units. It missed twelve opening words of the American Military University read, totaling 3.28 word-seconds within 3.48 segment-time seconds. Its remaining 11.34 missed continuous seconds were gaps between transcript segments. The proposed words of the other three ads were covered, although some remained unverified because of uncertain ASR or disagreement between model passes.
- **Stuff You Should Know:** Qwen selected every annotated commercial word interval, including the recovered Capital One passage. Its 12.98 missed continuous seconds were gaps between timed units. Recovered speech stayed outside the verified subset, as required. No protected discussion or network identity was proposed.
- **Diary Of A CEO, opening:** both models selected only 5.74 of 15.80 commercial word-seconds in the Wayfair read. The missing 10.06 word-seconds are a substantive content omission, not just silence between words.
- **Diary Of A CEO, closing:** Qwen proposed all annotated ad words but also 18.30 seconds of interview content and 1.66 seconds of free-show promotion. All protected suggestions remained unverified. Its verified ad subset covered 12.92 word-seconds.
- **Skeptoid failure:** Qwen exhausted bounded structured-response retries after 198.375 seconds. No predictions were scored. The run retains its 155.86 eligible commercial continuous seconds, 422.71 protected seconds, and 133.66 commercial word-seconds. Kev produced predictions, but missed content in the host's paid-event offers and proposed some protected introductory material.

Qwen's complete attempt therefore has **four successful clips, one failure and none pending**. Across all five references, 467.44 commercial continuous seconds and 410.24 commercial word-seconds were eligible; only 311.58 and 276.58, respectively, were scored for Qwen. The failure is neither a successful no-ad result nor silently removed from the eligible denominator. A later successful retry cannot replace this frozen failure.

All ten commercial units in Qwen's successful clips, and all thirteen in Kev's five clips, were touched by candidates. No continuous unit was completely covered. Some of that incompleteness is timestamp gaps: it does not mean every unit lost recognized commercial words.

## Protocol and limits

These are approximately fifty minutes from four user-selected shows, with opening and closing clips from the same Diary Of A CEO episode:

| Publisher source | Selection |
| --- | --- |
| [Stuff They Don't Want You To Know](https://omny.fm/shows/stuff-they-dont-want-you-to-know/listener-mail-sleeper-agents-devil-corp-automated-book-scanners-and-the-gandalf-protest) | August 27, 2026; first ten minutes, including the rutabaga sketch. |
| [Stuff You Should Know](https://omny.fm/shows/stuff-you-should-know-1/short-stuff-the-style-cycle) | October 7, 2026; first ten minutes of the fashion discussion. |
| [Skeptoid](https://skeptoid.com/episodes/1061) | October 6, 2026; first ten minutes, archaeology and commercial offers. |
| [The Diary Of A CEO](https://rss2.flightcast.com/xmsftuzjjykcmqwolaqn6mdn) | September 25, 2026; opening and closing excerpts of the Nicola Kilner replay. |

References were annotated from publisher context and local `small.en` transcripts before predictions. Paid products/events count as commercial; free show teasers are protected. **No human listening verification of labels or exact boundaries is claimed.** The v3 references use recovery transcripts and preserve their original versions. Dynamic ads can change audio and shift publisher timestamps; download identities, clip offsets and hashes are recorded.

Only labeled intervals are scored. Unresolved ranges are excluded, and unlabeled time is not presumed editorial. Source audio, transcripts, annotations and model evidence remain in ignored local files. Public artifacts contain metadata and numerical results. Independent atomic-interval reconstruction matched every scored view and unit, public/private totals, and frozen reference/transcript/audio hashes. This small convenience sample cannot establish accuracy on unfamiliar hosts, ads, accents or recordings.

The [initial freeze](evaluations/2026-10-08-candidate.json) records Qwen3-14B Q6_K and Kev 4B. The [model-only follow-up freeze](evaluations/2026-10-08-qwen35-candidate.json) records Qwen3.5-35B-A3B with unchanged `intent-boundary-v4` code and thinking disabled. Its Q4_K_M file was locally requantized from pinned Q8_0 weights; the first four layers' experts ran on the CPU. The follow-up was frozen before real-recording outcomes were inspected. Kev is a separate local model, not official Jev. No paid API requests were made.

Qwen3.5's five recording attempts took **1,456.11 seconds**, including the failed attempt; Kev took **116.64 seconds**. These classifier timings exclude download, transcription, model startup and audio rendering. They are not complete episode-processing benchmarks.

## Synthetic evidence and consumed holdouts

The original 27 cases are development/regression material. The subsequent [33-case fixture](../tests/fixtures/ad_read_holdout_v2.json), from eleven fictional groups, was frozen before initial predictions:

```text
SHA256 4581c6c745d33aceaa3dd4e238dc6358c3daf6a1e9ea88c80fa5de1fbabdf051
```

Its predictions have now been inspected. The Qwen3.5 rerun is **consumed-set regression, not a new holdout**. Thirty binary cases contain 306.8 authored commercial seconds and 501.4 editorial seconds; three ambiguous cases totaling 75 seconds are scored separately.

| Synthetic all-proposal measure | Qwen3-14B v4 | Kev v2 | Qwen3.5 v4 regression |
| --- | ---: | ---: | ---: |
| Commercial seconds missed | 3.2 | 0 | 0 |
| Editorial seconds selected | 89 | 71.8 | 59 |
| Exactly matching proposed cases | 20 / 30 | 22 / 30 | 25 / 30 |
| Editorial seconds selected by approval simulation | 25 | Not applicable | 0 |

The [initial Qwen run](evaluations/2026-10-08-fresh-holdout-qwen-v4.json) approved ten seconds of unpaid parody and fifteen seconds of quoted editorial advertising in its review-disabled simulation. Both model checks can agree incorrectly. Rules also approved 25 editorial seconds, while missing substantially more commercial time.

The [Kev run](evaluations/2026-10-08-fresh-holdout-kev-v2.json) proposed all labeled commercial time but added five parody seconds, ten quoted-editorial seconds, 39 coarse mixed seconds, 2.8 aligned mixed seconds, ten self-promotion-adjacent seconds and five paid-humor-adjacent seconds. Its adapter never auto-approves; zero approved cuts is not a competitive accuracy score. All 33 cases received review signals, which does not establish selective ambiguity recognition. Its [nine-case development run](evaluations/2026-10-08-kev-development-v2.json) covered all 69 commercial seconds plus eleven editorial seconds; five additional unresolved seconds in its ambiguous case were excluded from those binary totals.

The [Qwen3.5 regression](evaluations/2026-10-08-regression33-qwen35-v4.json) left all 59 extra editorial seconds unapproved: 39 in three coarse mixed units, ten around paid self-promotion and ten in unpaid parody. Its 34 unapproved commercial seconds were exactly those mixed units, which need human boundary edits. Approved intervals matched 27 of 30 cases. All humorous paid reads, quotations, delayed-return, word-aligned mixed and multiple-ad cases matched approved truth in this regression. Two ambiguous cases generated fifteen unresolved candidate seconds with no approvals; the third still received no uncertainty signal.

All three model runs returned 33 valid case results with no request failures. Classifier time was 497.30 seconds for Qwen3-14B, 21.89 for Kev, and 365.19 for Qwen3.5. Fixture-based duration, boundary, review and ambiguity metrics were independently checked. Known authored examples cannot establish real-podcast accuracy. The [October 4 Qwen 2.5 report](evaluations/README.md) remains a historical archive.

## Improvements validated in the editor

- New settings require approval for every cut; existing preferences remain respected. Candidate suggestions, two-pass verification and actual selection are distinct. Both passes use the same model, so agreement is not independent proof.
- Word timestamps permit sentence-level edits. Without them, mixed commercial/editorial sentences retain their coarse interval and require review instead of invented internal timestamps.
- Bounded gap recovery preserves original speech, visibly marks recovered text and prevents its automatic approval. Gaps can be silence, music or missed speech.
- Regenerating text preserves reviewed cuts for unchanged audio. Retry retains completed work. Fresh playback starts at 1x, and editing cleaned audio preserves the equivalent original playhead.
- Cut saves preserve detector/recovery metadata. Editing suggested bounds clears selection, retains the original detected interval and marks old evidence as applying only to those original times.

Browser regressions cover playback mapping, metadata, revisions and exports; mocked evaluator tests cover exclusions, failures, local-only calls and public-output redaction. A separate [export mechanics check](evaluations/2026-10-08-export-mechanics.json) removed a reference-selected 19.82 seconds from a 597.100437-second clip: retained PCM matched exactly, all 1,737 retained words mapped correctly, and each SRT/VTT had 108 valid cues. Source hashes stayed unchanged. These validate software/export behavior, not detection or listening quality.

**Prospective refinements are pending.** Changes to invalid-output recovery or adjacent-sentence handling are outside this checkpoint. Any focused rerun must identify its new candidate, retain this failure and disclose that the references/results have been inspected. Commands, reference formats and metric definitions are in the [evaluation guide](ad-read-evaluation.md).
