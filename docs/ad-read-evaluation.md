# Evaluating ad reads and editorial speech

This comparison checks whether Castwell can identify complete commercial passages while preserving editorial speech. A humorous paid advertisement is still commercial. An unpaid parody, a quoted advertisement in journalism, and an ordinary brand discussion can use the same words without being ads.

The evaluation uses local rules and, optionally, a locally running classifier through Castwell's existing OpenAI-compatible interface. The supplied challenge needs no speech model, audio download, or paid service. Its transcript text and timestamps are authored fixtures, so it cannot measure whether listening to vocal delivery improves detection.

## Frozen challenge

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

After freezing the candidate configuration, repeat that command with `--split eval` and a different output path. Use the actual model identity in `--model-label`, rather than the illustrative text above. To evaluate a separate supplied file, add `--fixtures /path/to/ground-truth.json`. The default backend is heuristic only; specify the split explicitly so a development run does not consume the evaluation split by accident.

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

For Jev, the review signal identifies uncertainty or conflicting answers. All its cut proposals require manual approval even when this signal is false. Compare `manual_review_cases` for cases containing unapproved proposals; do not treat provider review signals as equivalent estimates of a person's workload.

Report seconds alongside rates and their denominators. A high ad/not-ad classification score can hide a missing commercial lead-in or an editorial sentence appended to an otherwise correct ad cut. Display proposed and automatically approved results separately, and preserve the exact failed examples for inspection.

`threshold_shadow` is an additional hypothetical view of cuts with confidence at least `0.90`, retaining explicit review exclusions and any backend review gate. It does not authorize removal or change the actual approval result. Confidence values from different providers are not interchangeable calibrated probabilities, so a shared numeric threshold does not establish equal reliability.

For ambiguous cases, report approved seconds as unsupported automatic removal, not as measured editorial deletion. Do not use their empty references to improve the apparent number of correctly classified negatives.

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
