# Castwell: compact standardized model comparison

October 8, 2026 local time; completed October 9 UTC. Eight cases, five models and six combinations.

**Qwen3.8 is the strongest candidate here for protecting the conversation.** On the five recording excerpts, its proposals missed 1.00 of 133.04 labeled ad-word seconds and selected zero of 386.59 protected-word seconds. Qwen3.5 proposed every labeled ad word but also 10.68 protected seconds. Both preserved the two unpaid parody passages in their proposals. This is a useful tradeoff, not evidence that either model meets a zero-error production requirement.

**Official Jev worked, but did not improve this detector in the tested configuration.** Its proposals missed 15.38 ad-word seconds and selected 34.84 protected seconds. Adding Jev to either Qwen retained those additional false positives without recovering ads that Qwen missed. Local Kev4B and Kev9B were faster than Qwen but substantially less accurate on these excerpts. No tested combination improved its Qwen member under the fixed proposal and shortlist rules.

Use Qwen3.8 as the preferred next candidate for a reviewed workflow when preserving editorial speech matters most. Keep Qwen3.5 as the recall-oriented comparison baseline. Do not enable unattended cutting from this evidence. The existing Qwen3.5 application server was restored after testing; the evaluation did not change the application's default classifier or approve cuts. The compact result supersedes the earlier confounded comparison for the narrow question of matched Qwen package performance, while the [earlier report](local-model-round2-2026-10-08.md) remains historical evidence.

[Download the numeric results](evaluations/2026-10-08-compact-model-comparison.json). Every model completed all selected target units. There were 81 scored request slots across the five models, including eight reused Qwen3.5 responses, so the compact comparison required 73 new scored requests. The earlier interrupted large run is accounted for separately below.

## Pros and cons

| Setup | Advantages observed here | Costs and limitations | Recommendation |
| --- | --- | --- | --- |
| Qwen3.5-27B Q4_K_M | No missed ad speech in recording proposals; both parodies preserved; no paid API | 10.68 protected-word seconds proposed; roughly 15.2 minutes of recorded request time; substantial GPU memory | Recall-oriented baseline, with review |
| Qwen3.8-27B Q4_K_M | No protected speech proposed; both parodies preserved; nearly all labeled ad speech proposed | Missed 1.00 Skeptoid ad-word second; roughly 16.8 minutes of request time; more time requiring review than Qwen3.5 | Preferred conservative candidate, with review |
| Local Kev4B | No paid API; 48.78 seconds of recorded request time | Missed 88.46 ad-word seconds and proposed 48.04 protected seconds; no recording ads reached its shortlist | Do not replace Qwen with this setup |
| Local Kev9B | Better proposals than Kev4B; no paid API; 68.59 seconds of request time | Still missed 75.38 ad-word seconds and proposed 39.82 protected seconds; no recording ads reached its shortlist | Larger model did not justify replacing Qwen |
| Official Jev 1.13.0 | Valid structured responses throughout; 6.44 seconds of request time; approximately one cent | Missed ads and falsely proposed the July parody; transcript data goes to the hosted service; shortlist missed 104.10 ad-word seconds | Useful cheap experimental classifier, not an accuracy upgrade here |
| Qwen with either local Kev size | No additional paid API cost; disagreements can be surfaced | More false-positive proposals; no recording ads survived agreement-based shortlisting; two local runtimes to operate | No benefit under the tested combination rule |
| Qwen with Jev | Cheap second opinion without a second local GPU model | More false positives, more review work, and fewer ads shortlisted; did not recover the Qwen3.8 miss | Do not make this the default combination |

## The specific joking-ad question

Both Qwen models and Jev proposed all commercial speech in the authored humorous-paid-read and mixed-word-aligned cases, with no protected speech proposed. That success did not transfer equally to unpaid parody. On the two actual parody passages, proposed protected-word seconds were:

| Model | August 27 parody | July 30 parody |
| --- | ---: | ---: |
| Qwen3.5 | 0.00 | 0.00 |
| Qwen3.8 | 0.00 | 0.00 |
| Kev4B | 21.62 | 24.76 |
| Kev9B | 11.20 | 26.96 |
| Jev | 0.00 | 31.48 |

Jev's July false-positive proposal covered the whole 31.48-word-second labeled parody, plus other editorial speech in that selected excerpt. None reached its high-confidence shortlist. Across all five recordings, however, Jev shortlisted only 28.94 of 133.04 ad-word seconds. Both Kev shortlists selected no ad speech at all. Zero protected speech in a shortlist therefore does not by itself indicate a useful detector.

All five standalone models and all six combinations abstained on the entire 25-second ambiguous scenario, with no strict proposal or shortlist selection. Broad review-candidate coverage differed; the numeric artifact records it separately. Review includes abstentions even when they do not appear in the broader candidate list.

The compact run had no unavailable selected units. The preceding, incomplete 48-request Qwen3.5 run did contain one invalid response with a duplicated target ID and a missing target ID. It was rejected and preserved. It fell outside this selected scope and was not regenerated or counted as a success.

## Measured results

All durations below are seconds. A shortlist is a hypothetical model decision, not an approved cut. Review time includes speech outside the binary-labeled reference, so it need not equal the sum of missed ads and selected protected speech. Full definitions follow the tables.

### Five recording excerpts, ASR word seconds

Reference totals: 133.04 commercial seconds and 386.59 protected seconds. The ambiguous scenario is separate.

| Setup | Proposal ad seconds missed | Proposal protected seconds selected | Shortlist ad seconds missed | Shortlist protected seconds selected | Review or unavailable seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3.5 | 0.00 | 10.68 | 56.94 | 0.00 | 222.13 |
| Qwen3.8 | 1.00 | 0.00 | 56.94 | 0.00 | 270.64 |
| Kev4B | 88.46 | 48.04 | 133.04 | 0.00 | 556.19 |
| Kev9B | 75.38 | 39.82 | 133.04 | 0.00 | 535.37 |
| Jev | 15.38 | 34.84 | 104.10 | 0.00 | 230.30 |
| Qwen3.5 + Kev4B | 0.00 | 58.72 | 133.04 | 0.00 | 556.19 |
| Qwen3.5 + Kev9B | 0.00 | 50.50 | 133.04 | 0.00 | 549.55 |
| Qwen3.5 + Jev | 0.00 | 45.52 | 104.10 | 0.00 | 273.99 |
| Qwen3.8 + Kev4B | 1.00 | 48.04 | 133.04 | 0.00 | 556.19 |
| Qwen3.8 + Kev9B | 1.00 | 39.82 | 133.04 | 0.00 | 535.37 |
| Qwen3.8 + Jev | 1.00 | 34.84 | 104.10 | 0.00 | 282.28 |

### Two authored scenarios, scenario seconds

Reference totals: 27.60 commercial seconds and 18.00 protected seconds. The ambiguous scenario is separate.

| Setup | Proposal ad seconds missed | Proposal protected seconds selected | Shortlist ad seconds missed | Shortlist protected seconds selected | Review or unavailable seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3.5 | 0.00 | 0.00 | 0.00 | 0.00 | 27.60 |
| Qwen3.8 | 0.00 | 0.00 | 5.00 | 0.00 | 30.00 |
| Kev4B | 22.80 | 0.00 | 27.60 | 0.00 | 45.60 |
| Kev9B | 5.00 | 0.00 | 27.60 | 0.00 | 43.20 |
| Jev | 0.00 | 0.00 | 7.80 | 0.00 | 27.60 |
| Qwen3.5 + Kev4B | 0.00 | 0.00 | 27.60 | 0.00 | 45.60 |
| Qwen3.5 + Kev9B | 0.00 | 0.00 | 27.60 | 0.00 | 43.20 |
| Qwen3.5 + Jev | 0.00 | 0.00 | 7.80 | 0.00 | 27.60 |
| Qwen3.8 + Kev4B | 0.00 | 0.00 | 27.60 | 0.00 | 45.60 |
| Qwen3.8 + Kev9B | 0.00 | 0.00 | 27.60 | 0.00 | 43.20 |
| Qwen3.8 + Jev | 0.00 | 0.00 | 12.80 | 0.00 | 30.00 |

### Runtime and requests

| Model | Scored requests | Reused | Recorded HTTP request seconds | Paid API cost |
| --- | ---: | ---: | ---: | ---: |
| Qwen3.5 | 18 | 8 | 909.70 | $0.000000 |
| Qwen3.8 | 18 | 0 | 1008.02 | $0.000000 |
| Kev4B | 15 | 0 | 48.78 | $0.000000 |
| Kev9B | 15 | 0 | 68.59 | $0.000000 |
| Jev | 15 | 0 | 6.44 | $0.010462 |

## What was compared

Eight shared cases: two Stuff They Don't Want You to Know rutabaga parody excerpts, the first selected window from Diary of a CEO, Stuff You Should Know and Skeptoid, and three authored scenarios covering a humorous paid read, mixed word-aligned speech and ambiguity. These are selected diagnostic examples already used in development. They are not an unseen test set or a representative estimate of podcast accuracy.

The larger run was stopped at the user's request after 48 Qwen3.5 responses, including one structurally invalid response. Its captures remain preserved and the run remains incomplete. The smaller selection was fixed before collecting its Jev predictions. Qwen3.5 reused eight exact matching saved requests; ten were new. Qwen3.8 used 18 requests and each typed model used 15. Different request counts reflect API batching, with the same target units and surrounding context. Six combinations were computed from saved decisions, requiring no additional inference. No sensitivity sweep or repeated sampling was performed.

Both Qwen packages used the original v8 intent and boundary prompts, ordinary Q4_K_M quantization, native templates, 8,192 context tokens, seed 42, temperature 1, top-p .95, top-k 20, min-p 0, presence penalty 1.5, repetition penalty 1, thinking enabled with a 1,024-token reasoning budget, and a 4,096-token output cap. Native tokenizer preflight included the output reserve. Package templates, tokenizer details and quantization calibration differ, so this compares deployed packages rather than isolating base-model weights.

Kev4B, Kev9B and official Jev received the same typed questions and criteria. Each target had separate intent and boundary Choice questions, with commercial, editorial, mixed and uncertain outcomes. Jev's actual request differed from Kev's only in the pinned model identifier. Its raw provider identity remains official TypeSafe Jev. A documented model-name compatibility projection let the existing frozen typed decoder score its saved output; no answers or probabilities were changed. No reference labels were sent to any model, and prompts, labels and thresholds were not adjusted after predictions.

## Reading the results

**Proposals** require both decision heads to say commercial. **Shortlists** additionally require both selected-class probabilities, or Qwen's self-reported confidence, to reach .90 and satisfy alignment gates. These confidence measures are not calibrated equivalents. Every real cut still requires user approval. The evaluated shortlists are hypothetical and are not exported audio.

For a combination, proposals are the union of the members' commercial proposals. A shortlist requires both members to agree with sufficient confidence. Disagreements require review. Both members must be available; a missing result is not silently replaced with the other model. This is a fixed combination rule, not a third model judging the first two, and agreement is not proof of independent confirmation.

Lower missed-ad time and lower protected-speech selection are better. A model that selects nothing can avoid false cuts while missing every ad. The tables therefore show both quantities, along with time requiring review or lacking an available decision. The broader review-candidate view and per-case results are retained in the numeric artifact. Abstentions and errors remain in planned denominators.

Recording measurements use the union of ASR word intervals intersected with reference intervals. Authored scenarios use their scripted timeline; their seconds are reported separately. Coarse transcript segments cannot invent precise sentence boundaries. Only the declared selected target scope is scored, and context outside that scope is not counted as a correct negative. Some speech is unlabeled or excluded from the binary reference. The ambiguous scenario is evaluated separately for review and inappropriate confident selection.

Reference labels and ASR word timing remain provisional, without human acoustic verification. This experiment tests transcript interpretation; none of these model arms directly evaluated vocal delivery. It cannot establish zero missed ads, zero lost editorial speech, or safe unattended production removal.

## Runtime, cost and evidence

The local machine used an RTX 3090 Ti with 24 GB VRAM, an i9-12900K and 128 GB RAM. Qwen ran through native llama.cpp b11146, commit `7fe450e19`, CUDA 12.4, with one request slot and all layers on the GPU. Kev used its isolated Torch 2.8 CUDA 12.8 environment, BF16 and SDPA, with optional fused kernels and CUDA graphs disabled. Local models ran sequentially on the shared GPU. Their paid API cost is zero; electricity and hardware costs were not measured.

| Package | Pinned identity |
| --- | --- |
| Qwen3.5-27B Q4_K_M | GGUF SHA256 `84b5f7f112156d63836a01a69dc3f11a6ba63b10a23b8ca7a7efaf52d5a2d806` |
| Qwen3.8-27B Q4_K_M | GGUF SHA256 `7e78da5d7e3ae28d178121f58646953305f3e5bd3cb46f4a75584e8b6c6fe169` |
| Kev4B | Checkpoint `6cfce5c2fa4b4bd64026336ab649c5ca78857d52` |
| Kev9B | Checkpoint `db029f08b290afd9fee4aa4bbcd9ae48602d1eb0` |
| Kev source | `5e42a7a03f28134853dd3ff77461457e921e5ec1` |
| Official TypeSafe Jev | `jev-1.13.0`, official `/v1/systemone` endpoint |

Jev returned valid responses for all 15 requests and reported 249,088 input tokens. At the documented $0.042 per million input tokens, with output tokens free, that is **$0.010461696**, approximately one cent. This is a documented-rate usage estimate, not a provider invoice. The run used a $0.50 local ledger ceiling within the earlier $5 authorization. No request has unknown billing status. Rates and token limits were checked against the official [API documentation](https://docs.typesafe.ai/api) and [model documentation](https://docs.typesafe.ai/models) on October 9, 2026 UTC.

Timings below sum recorded HTTP request durations. They exclude downloads, server loading, hash verification, readiness probes, postprocessing and enforced API pacing. Reused Qwen3.5 requests retain their original recorded durations. Combined timings in the numeric artifact sum member durations rather than measuring a parallel production deployment. No latency distribution or repeated-run stability estimate is claimed.

The new [Jev transport](../castwell/jev_transport.py) pins the official endpoint and model, refuses redirects and automatic retries, and reserves worst-case input cost in an append-only ledger before each paid request. Unknown attempts retain their reservation and are never automatically resent. Its 65 mocked transport controls passed. The real API check is the 15-request run above. This transport was used by the compact evaluation runner; the existing application detector and older evaluation CLI were not switched to this experiment automatically.

The supplied credential is stored locally using Windows CurrentUser DPAPI, under an ignored secrets directory. It is absent from source, reports and Git. Raw podcast transcripts, reference intervals and model responses stay in ignored local evaluation storage. The public numeric artifact contains aggregate and per-case measurements plus integrity hashes. It supports inspection of the reported arithmetic; independent reproduction of the recordings still requires the private inputs or new licensed transcripts and references.
