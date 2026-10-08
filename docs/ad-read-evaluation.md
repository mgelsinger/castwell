# Evaluating ad reads and editorial speech

This comparison checks whether Castwell can identify complete commercial passages while preserving editorial speech. A humorous paid advertisement is still commercial. An unpaid parody, a quoted advertisement in journalism, and an ordinary brand discussion can use the same words without being ads.

The evaluation supports local rules, verified contextual AI and the separate local Kev typed adapter. Synthetic challenges need no speech model, audio download, or paid service. Their text and timestamps are authored, so they cannot measure whether listening to vocal delivery improves detection. See the [October 8 readiness checkpoint](readiness-2026-10-08.md) for versioned results. V6 completed all five recordings but remains inadequate for unattended cuts: it proposed 155.80 protected word-seconds, verified 6.20 of them, and missed 54.64 commercial word-seconds. The preserved Qwen3.5 v4 run completed four clips and failed response validation on Skeptoid; Kev completed all five. The v4 failure's eligible durations remain explicit.

## Challenge versions and holdout status

The original 27 cases described below are now **development/regression only**, including rows still marked `split: "eval"`. Their predictions have been inspected. Those historical split names remain unchanged so the archived results can be reproduced; they no longer establish a fresh holdout.

The new input is [`tests/fixtures/ad_read_holdout_v2.json`](../tests/fixtures/ad_read_holdout_v2.json): 33 synthetic cases, all marked `eval`, from 11 fictional groups disjoint from the old challenge. It includes three cases each in 11 categories, adding `mixedwordaligned` and `multispan` to the categories below. `mixedwordaligned` supplies authored word timestamps; the ordinary `mixedspan` cases do not. It was frozen before the new candidate predictions:

```text
SHA256 4581c6c745d33aceaa3dd4e238dc6358c3daf6a1e9ea88c80fa5de1fbabdf051
```

The [October 8 candidate record](evaluations/2026-10-08-candidate.json) pins the model, code and reference identities. The [Qwen v4 predictions](evaluations/2026-10-08-fresh-holdout-qwen-v4.json) and [Kev v2 companion predictions](evaluations/2026-10-08-fresh-holdout-kev-v2.json) have now been inspected. The first Qwen run was held out; the set is now regression/development material for any subsequent tuning or candidate changes. Neither fictional grouping nor authored word alignment demonstrates generalization to real hosts.

The [Qwen3.5 model-only candidate](evaluations/2026-10-08-qwen35-candidate.json) was chosen after those results. Its [33-case rerun](evaluations/2026-10-08-regression33-qwen35-v4.json) is explicitly a regression: zero simulated approved editorial seconds on these known cases does not restore their held-out status. The same fixed policy/code was used for the subsequent real-recording check.

Later policy changes also use these consumed inputs. [V5](evaluations/2026-10-08-regression33-qwen35-v5.json) was rejected for five simulated approved editorial seconds and ten unsupported approved seconds in an ambiguous case; it never attempted real recordings. The [v6 freeze](evaluations/2026-10-08-qwen35-v6-candidate.json) precedes its [33-case regression](evaluations/2026-10-08-regression33-qwen35-v6.json) and five-recording regression. V6 proposed all 306.8 commercial seconds plus 59 editorial seconds, with zero simulated approved editorial seconds, 34 commercial seconds left unapproved in coarse mixed segments, and zero ambiguous approvals. All three ambiguous cases signaled review. Neither these repeated examples nor the now-inspected real recordings constitute a fresh holdout.

### Original challenge, retained for reproducibility

The input is [`tests/fixtures/ad_read_challenge.json`](../tests/fixtures/ad_read_challenge.json). It was frozen before running the comparison:

```text
SHA256 9bc08a5ade15ee3a78ec7f9d066ad8136cc60c3002f27f1561993a5627503266
```

There are 27 cases: nine development cases and 18 evaluation cases. Each of the following categories has one development case and two evaluation cases:

| Category | Intended distinction |
| --- | --- |
| `straightad` | A complete, explicit paid sponsor read, including its closing commercial speech. |
| `humorouspaidad` | A real paid pitch performed with jokes. The jokes do not erase its commercial purpose. |
| `unpaidparody` | An explicitly unpaid fictional sales sketch that should remain in the episode. |
| `quotededitorial` | Advertising quoted as evidence in reporting, research, or criticism. |
| `ordinarybrandmention` | Independent product discussion or consumer advice without a commercial pitch. |
| `selfpromo` | The host selling a paid class, workshop, or membership. |
| `delayedreturnboundary` | A completed advertisement followed by editorial speech before a later return marker. |
| `mixedspan` | Commercial and editorial speech sharing a segment without word timestamps. |
| `ambiguous` | Missing evidence about the speaker's arrangement or purpose; review is required. |

Development uses three fictional podcast/host groups; evaluation uses six different groups. No group appears in both splits. These names establish a grouping convention, not evidence of generalization to unfamiliar real hosts. All cases were written for this challenge, and two evaluation cases per category are far too few for a broad accuracy claim.

The brands and podcast identities are fictional. The fixture is text only. Its normal segments occupy authored five-second slots; mixed-span cases use explicit alternative timings. No times were measured from recordings.

Do not change labels, prompts, thresholds, or model settings in response to evaluation results and then present a rerun as untouched evaluation. Make choices on the development split, freeze the candidate configuration, and evaluate that configuration once. If results prompt another change, disclose that the split has been inspected and prepare a new held-out set for the next comparison. A changed fixture needs a new hash.

## What counts as a correct decision

`expected_intervals` describes the commercial speech in cases with a known label. Empty intervals mean an editorial negative only when the category is not `ambiguous`.

For `ambiguous` cases, empty intervals mean no automatic cut is authorized by the available evidence. They do not establish that the passage is editorial. Exclude these cases from ad-versus-editorial accuracy denominators and report automatic cuts and review behavior separately.

`expect_review` is an independent requirement. It is true for ambiguous cases and mixed-span cases. A commercial passage can be known to exist while its safe cut boundaries remain uncertain. In the mixed-span fixtures, the author supplies reference times inside one segment, but the classifier receives no word alignment that locates those internal boundaries. Report these cases separately as an information limitation; selecting the whole segment can remove editorial speech, while a coarse transcript cannot establish the precise safe cut.

An ordinary `expect_review: false` case does not demand automatic approval. A correct suggestion left for human review may still be useful. Track the review workload separately from whether the suggested span is correct.

## Compare the same inputs

1. Preserve the fixture hash, code revision, model identity, inference settings, prompt, approval threshold, and review policy in the result record.
2. Run the local-rule baseline on the selected split. It makes no classifier requests.
3. When a local model is available, run the candidate on the same transcripts and split, with the same approval policy. Use a loopback endpoint and no provider credentials.
4. Keep all proposed intervals, confidence scores, approval decisions, errors, and elapsed times. A timeout or invalid model response is a failed case, not a successful no-ad result.
5. Inspect development cases to choose a candidate configuration. Freeze that configuration before inspecting evaluation predictions. Keep evaluation results by category and case, even when an overall number is also reported.

Run the development baseline from a checkout with the project dependencies available:

```sh
python scripts/compare_detectors.py --backend heuristic --split dev --output development-rules.json
```

If an existing local classifier is listening on port 8081, compare both variants on development cases:

```sh
python scripts/compare_detectors.py --backend heuristic --backend local-ai --split dev --base-url http://127.0.0.1:8081/v1 --model castwell-local --model-label "exact model, quantization, and version" --output development-comparison.json
```

For development on all 27 inspected cases, use `--split all`; `--split dev` selects only the original nine-case subset. `local-ai` retains the legacy classifier. The new two-pass policy uses `--backend verified-ai`. To run the separately frozen 33-case challenge, explicitly select its file:

```sh
python scripts/compare_detectors.py --backend heuristic --backend verified-ai --fixtures tests/fixtures/ad_read_holdout_v2.json --split eval --base-url http://127.0.0.1:8081/v1 --model castwell-local --model-label "exact frozen model and runtime" --output .local/frozen-synthetic-results.json
```

Both provided fixture files are now consumed regressions. For a genuinely new holdout, freeze it and the candidate before predictions, and disclose that inspecting its results consumes it for subsequent tuning. Use the actual model identity in `--model-label`. The default fixture is still the old 27-case file and the default backend is heuristic only. Use `--fixtures` and `--split` explicitly. Results from synthetic shadow approval are separate from the application's default review requirement.

Change one source of behavior at a time. Replacing transcription, changing a prompt, adding a judge, and lowering the approval threshold in the same comparison would prevent attribution of any improvement. Castwell's current classifier receives transcript text; this challenge compares textual reasoning and boundary selection only.

## Measure the mistakes that affect listening

Use the union of intervals so overlapping cuts are not counted twice. For a case with known commercial intervals `A` and automatically approved cuts `C`:

- **Editorial seconds deleted:** duration of `C` outside `A`. This measures the damaging error of removing part of the episode.
- **Commercial seconds missed:** duration of `A` outside `C`. Report the same comparison against all proposed cuts too, so a deliberate review decision is distinguishable from a detection miss.
- **Boundary error:** the start and end offset of matched predicted and reference spans. Report unmatched and extra spans separately; no prediction must not appear as zero boundary error.
- **Review behavior:** whether a case requiring human review produced a reviewable decision, and how many other cases were sent for review. Silence on an ambiguous case is not affirmative recognition of uncertainty.
- **Operational cost:** elapsed time, request failures, and local resource needs. Faster or cheaper behavior is useful only alongside its content errors.

The harness merges touching or overlapping spans for scoring. For boundary errors, it greedily pairs positively overlapping predicted and reference spans by highest intersection-over-union, then greatest overlap, then earlier indices. Each span can match once. It reports absolute start and end errors, their mean across matched boundaries, and separate unmatched counts. Duration errors use interval unions independently of this pairing.

For the heuristic and local classifier backends, observed review means at least one proposed cut is unapproved or marked `requires_review`. An empty output therefore does not count as a review decision, even when review was expected. This measures the current interface's review signal, not whether a person actually reviewed the audio.

For Jev and local Kev, the review signal identifies uncertainty or conflicting answers. All their cut proposals require manual approval even when this signal is false. Kev uses selected-class probability for its review threshold and records its normalized confidence margin separately. Compare `manual_review_cases` for cases containing unapproved proposals; do not treat provider review signals as equivalent estimates of a person's workload.

Report seconds alongside rates and their denominators. A high ad/not-ad classification score can hide a missing commercial lead-in or an editorial sentence appended to an otherwise correct ad cut. Display proposed and automatically approved results separately, and preserve the exact failed examples for inspection.

`threshold_shadow` is an additional hypothetical view of cuts with confidence at least `0.90`, retaining explicit review exclusions and any backend review gate. It does not authorize removal or change the actual approval result. Confidence values from different providers are not interchangeable calibrated probabilities, so a shared numeric threshold does not establish equal reliability.

For ambiguous cases, report approved seconds as unsupported automatic removal, not as measured editorial deletion. Do not use their empty references to improve the apparent number of correctly classified negatives. A case can contain both review suggestions and approved cuts, so a positive review flag does not establish that all its uncertain speech was protected.

## Real recordings with partial references

[`scripts/evaluate_recordings.py`](../scripts/evaluate_recordings.py) evaluates private reference/transcript pairs without changing the library or rendering audio. Repeat `--reference` for each clip. The October 8 corpus contains five excerpts from four shows; its v3 references were aligned to the provisional recovery transcripts before classifier predictions. Audio, transcripts and full evidence remain local and ignored by Git.

```sh
python scripts/evaluate_recordings.py --backend verified-ai --reference .local/real-podcasts/stdwytk-2026-08-27.first-10m.reference-v3.json --base-url http://127.0.0.1:8081/v1 --model castwell-local --model-label "Qwen3.5-35B-A3B local Q4_K_M, exact frozen runtime and intent-boundary-v6" --model-file .local/models/qwen3.5-35b-a3b/Qwen3.5-35B-A3B-Q4_K_M-local.gguf --candidate-config docs/evaluations/2026-10-08-qwen35-v6-candidate.json --output .local/evaluations/recording-private.json --summary-output .local/evaluations/recording-summary.json
```

The reference identifies a transcript and its SHA256, `commercial_units`, `protected_units` and `unverified_intervals`. The local source manifest identifies audio hashes and clip offsets. Commercial and protected intervals may not conflict outside excluded ranges. The evaluator checks these identities and records model-artifact/code hashes; changing them prevents resuming the same checkpoint. Use `--resume` only to continue unfinished clips with matching inputs. Successful and failed clips remain recorded; a failure is not silently retried as a new successful case.

Scoring uses interval unions and subtracts unverified ranges from both reference classes. It reports commercial seconds covered/missed, protected seconds selected, any/full coverage of each annotated unit, and selected unverified or unlabeled seconds. Unlabeled audio is never presumed editorial. Failed and unfinished clips retain explicit eligible-duration totals; per-view measured totals cover successful clips. These provisional references do not support exact acoustic boundary claims or whole-episode ad recall.

The recording report separates **candidates**, **verified** suggestions and **approved** cuts. Review is enabled for these runs, so approval should be empty even when useful ads are found. Qwen's verified view requires intent/boundary agreement plus alignment safeguards. Both judgments come from the same model and are correlated. For Kev, verified metrics are `null` with `view_availability.verified: false`; this means no such verifier exists, not that Kev failed to find any ads.

The independently derived [v6 word-time comparison](evaluations/2026-10-08-real-word-time-coverage-v6.json) intersects the union of provisional ASR word intervals with labeled commercial/protected spans, excluding unverified ranges. It reports selected and missed word-seconds alongside unchanged continuous-span metrics. This distinguishes recognized word coverage from timestamp gaps, but cannot score speech absent from ASR or establish listening-verified acoustic truth. V6 and Kev compare on the same five clips. Comparisons involving v4 use the same four successful clips, while its failed Skeptoid prediction and five-clip eligible denominators remain explicit. Each pair requires identical reference, transcript, clip, source-audio and offset identities. The [earlier v4 supplement](evaluations/2026-10-08-real-word-time-coverage.json) is preserved separately. Later results must not replace a frozen failure or combine different decoding candidates under one score.

Use `--backend kev --base-url http://127.0.0.1:8083 --model kev-latest` for the local typed adapter. Repeat `--model-file` for its adapter weights, head and base-weight shards, and supply the matching identity in `--model-label`. The private report preserves typed decisions, provider metadata and selected probabilities. The public summary excludes transcript text, model reasons/evidence and raw error bodies. Kev is a separate local model, not official Jev, and its confidence margin is not interchangeable with Qwen's self-reported confidence.

Only literal loopback endpoints are allowed by this recording tool. It follows no redirects and inherits no provider credentials or proxy configuration. There is no paid-provider switch. The separate synthetic Jev adapter below remains disabled without explicit paid opt-in; no paid requests were made for the October 8 work.

## Optional Jev comparison and cost

Jev is a separate, text-only evaluation adapter in `castwell/jev.py`. It is not connected to the application's automatic detector or your library. It asks separate questions about commercial intent, unpaid parody or quotation, and humor. Humor alone never vetoes a real commercial passage. Conflicting judgments or uncertain intent produce a review signal; every returned cut remains unapproved.

The adapter records the Choice distribution, the API's separate confidence statistic, parody and humor scores, model identity, policy version, and reported token usage. These scores are model outputs, not measured accuracy. It uses the same available transcript timing units as the existing detector, so it cannot recover precise boundaries from a segment without word alignment.

Live Jev calls are disabled unless both `--allow-paid-api` and `--jev-api-key-env NAME_OF_YOUR_KEY_VARIABLE` are supplied. The named variable must contain a key. Every requested backend is validated before any inference begins. Jev accepts only the official HTTPS endpoint and never follows redirects or automatically retries a potentially billable request. A failed request is recorded as a failed case; the provider may still charge for work it performed before that failure.

As checked on October 4, 2026, TypeSafe lists `jev-1.13.0` at **$0.042 per million input tokens**, with output tokens free. Testing it requires access to that paid API. Token usage includes the transcript and all question instructions; extra questions add input tokens. No live Jev evaluation was run for this setup. See [current models and pricing](https://docs.typesafe.ai/models), [Choice response semantics](https://docs.typesafe.ai/primitives/choice), and [confidence semantics](https://docs.typesafe.ai/confidence) before deciding to fund a comparison.

The local Qwen comparison needs disk space, memory, and an already running llama.cpp server. It makes no paid API calls. The command defaults to a literal loopback endpoint and does not inherit hosted-provider credentials. See the [native launcher instructions](../README.md#run-the-classifier-locally).

## Add user-supplied ground truth

The harness input uses this JSON structure. The example below illustrates the format; it is not another evaluation case or measured recording:

```json
{
  "schema_version": 1,
  "scope": "User-supplied clips with manually reviewed commercial boundaries.",
  "cases": [
    {
      "id": "clip-001",
      "split": "eval",
      "group": "podcast-a--host-a",
      "category": "humorouspaidad",
      "description": "Payment is disclosed in the source; a human reviewer marked the complete paid read.",
      "transcript": {
        "language": "en",
        "duration": 16,
        "segments": [
          {"id": 0, "start": 0, "end": 4, "text": "Today we are discussing the station clock."},
          {"id": 1, "start": 4, "end": 8, "text": "This episode is sponsored by Pocket Planet, which paid for this message."},
          {"id": 2, "start": 8, "end": 12, "text": "Use code POCKET for a free trial. My disorganized bag is delighted."},
          {"id": 3, "start": 12, "end": 16, "text": "Now back to the clock and the engineer who repaired it."}
        ]
      },
      "expected_intervals": [{"start": 4, "end": 12}],
      "expect_review": false
    }
  ]
}
```

For real clips, use times measured on the original audio timeline. Preserve enough speech before and after each candidate passage to determine context and boundaries. Have a reviewer establish commercial purpose and intervals before seeing model predictions; record uncertainty instead of forcing a label. If reviewers disagree about intent or a boundary, resolve the disagreement or retain a review-required case.

Keep all clips from the same host or podcast in one split. Where a host appears on multiple podcasts, use one shared grouping key. Keep repeated or near-identical sponsor scripts together as well, even across hosts. A random split of neighboring clips would otherwise leak the same speaking style, context, and advertising copy into both development and evaluation.

A future audio-aware comparison needs the same recordings, human references, and disjoint groups for both variants. It should directly test whether adding audio reduces editorial deletion or missed commercial speech. Results from this synthetic transcript set cannot establish that vocal tone, laughter, sarcasm, or a particular speech evaluator provides that improvement.
