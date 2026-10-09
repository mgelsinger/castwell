# Castwell: a bounded policy pilot

October 9, 2026. Eight cases, two models, two fixed policies.

**Keep Qwen3.8 with the existing v8 policy and manual approval.** The v9 clarification preserved the tested parodies, but caused a new boundary disagreement on 0.96 seconds of a real warranty-ad slogan. Those words still appeared for review; they no longer qualified as strict commercial proposals. This failed the deployment check, so v9 was not activated.

**Official Jev did not become a better default with the revised instructions.** Both Jev versions proposed 34.84 protected-word seconds in the July fake-ad excerpt. The revision also increased missed commercial speech on the known recordings. Its speed and low price remain useful for experiments, but do not compensate for these errors.

The [numeric results](evaluations/2026-10-09-policy-v9-pilot.json) retain every selected case, both reference versions, proposals, hypothetical shortlists, availability and review workload. The [preceding comparison](standardized-model-comparison-2026-10-08.md) covers Qwen3.5, Qwen3.8, local Kev4B, local Kev9B, official Jev and six combinations, including each setup's pros and cons. That historical report is unchanged.

## Paired results

These are ASR word seconds, not wall-clock episode duration. Lower values in both error columns are better. Known excerpts contain 65.92 labeled commercial seconds and 199.91 protected seconds under the clarified reference. The four new windows contain 32.70 primary commercial seconds and 251.88 protected seconds.

| Setup | Known ad seconds missing from proposals | Known protected seconds proposed | New ad seconds missing from proposals | New protected seconds proposed | Available cases |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen3.8 v8 | 0.00 | 0.00 | 0.00 | 0.00 | 7/8 |
| Qwen3.8 v9 | 0.00 | 0.00 | 0.96 | 0.00 | 8/8 |
| Jev v8 | 4.32 | 34.84 | 0.00 | 0.00 | 8/8 |
| Jev v9 | 22.30 | 34.84 | 0.00 | 0.00 | 8/8 |

**Qwen v8's zeros do not mean flawless completion.** Its new Skeptoid response duplicated a target ID and omitted another. The decoder rejected that case and counted its unavailable speech as requiring review. That window has no primary commercial reference, so its failure does not increase the missed-ad column. The original failure remains in these results even if operational recovery later succeeds.

All four setups proposed the complete 20-second commercial passage and none of the 10 protected seconds in the separately authored humorous-paid-read scenario. Synthetic scenario seconds are not added to recording word seconds.

The .90-confidence shortlists were substantially less complete than proposals. On known recordings, Qwen v8 left 14.00 commercial seconds outside its shortlist; v9 left 59.36. Jev v8 left 55.42, and v9 left 59.36. No tested shortlist selected protected speech, but this was not evidence of complete ad removal. Shortlists are simulated selections, not approved or exported cuts.

## What changed and what stayed fixed

The [v9 candidate](cut-policy.md) explicitly protected standalone greetings and descriptive previews of bonus coverage without a purchase invitation. It retained genuine paid jokes, sponsor introductions, persuasive setup and explicit paid offers as commercial. The same clarification was added to both Qwen decision prompts and Jev's typed questions. Qwen's model file, native template, sampling, context, reasoning budget, output allowance, seed and the .90 threshold stayed fixed. There was no threshold sweep, repeated sampling or fine-tuning.

One known Skeptoid reference had counted a standalone speaker greeting as commercial. The experiment preserved the original reference and created a separate clarified copy that protects its 1.00 ASR word second. **Both model versions were scored against both copies.** The table uses the clarified reference for both. The resulting reduction from the earlier Qwen3.8 missed-ad figure is relabeling, not improved detection.

The selected development cases were the October 6 Skeptoid window, both previously examined Stuff They Don't Want You to Know parody excerpts, and one authored humorous paid read. New cases were the first canonical windows selected from the October 8 Diary of a CEO, Stuff They Don't Want You to Know and Stuff You Should Know recordings, plus September 22 Skeptoid. Their references and selected scopes were frozen before their first predictions. The source excerpt filenames include `first-210s`, but only their declared first windows were scored.

Previously consumed ad scripts were excluded from primary new-copy scoring. Excluded and unlabeled speech was not counted as correct editorial classification. Only one new primary ad remained, comprising 32.70 ASR word seconds. These references use transcripts and publisher context, without human listening verification. This is a modest prospective check, not representative evidence across whole podcasts.

Qwen used the pinned ordinary Q4_K_M package documented in the [local setup](local-models.md#qwen38-27b-reviewed-profile). Jev used the official TypeSafe `jev-1.13.0` endpoint. Both judged the same target speech with surrounding context, using commercial, editorial, mixed and uncertain outcomes. Qwen self-reported confidence and Jev class probabilities are not calibrated equivalents. No reference labels were supplied to either model.

Proposals require both heads to classify a unit as commercial. Shortlists additionally require confidence and alignment checks. Disagreements and unavailable responses remain review work. The frozen decision required avoiding extra protected-speech proposals and extra missed commercial speech before switching the application; the published check also inspects each case separately. The warranty slogan regression prevented that switch.

## Requests, cost and preserved evidence

| Run | Scored request slots | Reused exact requests | New requests |
| --- | ---: | ---: | ---: |
| Qwen3.8 v8 | 18 | 10 | 8 |
| Qwen3.8 v9 | 18 | 0 | 18 |
| Jev v8 | 17 | 9 | 8 |
| Jev v9 | 17 | 0 | 17 |

That is 26 new local benchmark requests and 25 new paid requests. Different API batching produces different request counts for the same target units. Reuse required exact matching requests and preserved the original captures. One local startup probe and the separate recovery check below are outside the benchmark.

The new Jev requests cost an estimated **$0.022924566**, with no billing-unknown attempts, against a $0.25 local ledger ceiling. This uses the documented $0.042 per million input tokens and free output tokens, checked in the official [API](https://docs.typesafe.ai/api) and [model](https://docs.typesafe.ai/models) documentation on October 9 UTC. It is a usage estimate, not an invoice. Including the preceding compact comparison gives approximately $0.03339 for those two completed tests.

The frozen pilot manifest SHA256 is `3edaa15a9d051948852adb74b977bf59b54e655c908775fff27f53dbf5fb2186`; the private result SHA256 is `8aedb382256d218ef41562fe80f65e39ff636e5e3af5945dd503aa641b05f156`. Original responses, failure captures, transcripts and references remain in ignored local storage. The public JSON contains numeric results and integrity hashes, without transcripts or credentials.

## Operational result

A separate check replayed the recorded invalid v8 response into the application's existing recovery path, then allowed at most six new local requests. The app split the failed 24-unit window into two 12-unit windows, obtained valid intent and boundary decisions for both, and completed the remaining window. It returned 15 unapproved review suggestions, left the transcript unchanged and saved nothing to the library. This demonstrates recovery from that specific malformed response; it does not replace the benchmark failure or establish classification accuracy for the recovered suggestions.

The Qwen3.8 setup helper and launcher now pin and verify the tested model and llama.cpp binary. The launcher passed validation against the installed files. The application was restarted on its existing library, and both HTTP services passed health checks. All library records matched their pre-update hashes: five episodes, nine cut revisions, saved settings and the feed table. No episode or cut approval was changed.

The local offline suite passed 573 tests and 121 subtests, including the setup helpers, classifier validation and application integration. It made no paid model calls. The active intent and boundary prompts and request settings were also compared directly with the captured v8 requests and matched. The reasoning-profile metadata and settings label now describe both supported dense Qwen versions instead of incorrectly naming only Qwen3.5.

Qwen3.8 is the selected local model. The v8 classification policy, .90 threshold and requirement to approve every cut remain active. The experimental v9 text is retained for inspection and is not imported by the application. Unattended production cutting and sample-accurate boundaries remain unproven; a reviewed workflow is the supported result.
