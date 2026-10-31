# Listening feedback and podcast-promo corrections

The owner reviewed 17 of the 18 numbered pairs in the saved July listening packet. Check 04 was deferred because it covered the other end of the podcast promo flagged in Check 03. An expanded Check 06 supplied additional context across one complete ad break. These are development excerpts from a previously consumed recording, not a fresh whole-episode acceptance test.

| Finding | Checks | Result |
| --- | --- | --- |
| Acceptable or clean | 01, 02, 05, 06, 07, 08, 09, 10, 11, 12, 13, 15, 16, 18 | Owner accepted these excerpts; Check 06 used the expanded comparison. |
| Retained podcast promotion | 03, 14, 17 | Health versus Hype, the iHeart/Apple Podcasts catalogue invitation, and The Haunted Library require correction under the expanded scope. |
| Deferred | 04 | Recheck with the corrected Health versus Hype transition. |

The owner accepted short chopped ad-word endings heard between several segments and asked that removing them not substantially change or break the workflow. No additional padding or gap merging was introduced. The five-second-per-hour target remains a target: the listener's estimate of a few seconds is not a measured residual-ad total. No program cut was reported in the reviewed excerpts; this does not establish zero show loss across the recording.

Current source clarifies that genuine network catalogue invitations and their associated app/platform calls to action are podcast promotions even without naming one particular show. Neutral production attribution, current-show-only follow directions, unpaid parody and fake ads remain protected. Classifier policy revisions invalidate prior saved AI analyses; inference settings and the shared removal-scope identity remain unchanged.

The software checks cover the policy change and saved-analysis/evaluation integration: 502 tests and 67 subtests passed across the two focused suites. Mocked classifier tests establish software behavior, not model accuracy.

The first actual local CUDA diagnostic used the exact cached transcript underlying the listening packet, with Qwen3.8-27B Q4_K_M on 66/66 GPU layers. It made two local generation requests and no paid calls. Detection including model startup took 225.77 seconds; rendering a separate proposed copy took 31.79 seconds. These omit fresh speech recognition and cannot pass the end-to-end speed gate.

That first result was rejected. It selected all three target promos but also newly selected an editorial reference to earlier related-show coverage, an explicitly unsponsored listener-gift discussion, and a bridge word at the show's return. The proposals and request captures remain diagnostic evidence; they were not applied to the library or promoted to a package. The neutral production credit, closing publisher bumper and rutabaga callback remained outside those cuts. A second bounded diagnostic used generic editorial-versus-promotion clarification; no episode names, brand names or timestamps are classifier rules.

| Actual local diagnostic | Detection and model startup | Preview rendering | Generation requests | Review result |
| --- | ---: | ---: | ---: | --- |
| Fast v8, catalogue clarification | 225.77 s | 31.79 s | 2 | Rejected: unrelated show selections |
| Fast v9, editorial distinction clarified | 226.25 s | 34.89 s | 2 | Only intended promo changes; both corrected excerpts accepted |

The second run changed exactly 187 word decisions in the three intended promotion passages. Every other word decision matched the previously reviewed baseline; all prior cut coverage was retained, and no word remained unresolved. This includes preserving the incidental related-show reference, listener-gift discussion, show-return bridge, rutabaga callback, production attribution and closing publisher bumper. This is a proposal comparison, not a claim that all acoustic boundaries or all advertising in the full recording have been independently verified.

Both runs used the same full cached transcript, local model, context size and reasoning budgets. Four generation requests were made in total, all local, with zero paid calls and zero new ASR requests. Each owned classifier was stopped after detection. The first rejected result remains in the [machine-readable evidence](2026-10-31-listening-corrections.json). No detector results were replaced by manual timestamp overrides.

Two corrected listening comparisons were extracted from the actual full proposed outputs using their respective cut maps. The updated first-break transition lasts 34.92 seconds and the updated closing sequence lasts 25.83 seconds, about one minute together. The owner listened to both updated clips and reported each acceptable. These cover the Health versus Hype correction and the closing catalogue/Haunted Library corrections. Their corresponding old outputs remain available privately but were not presented in this recheck. Original findings remain recorded; no earlier observation was relabeled. The separate complete fake-rutabaga sketch still needs its own current-policy check; the July callback is not a substitute.

Acceptance applies to these two corrected excerpts, not the complete recording or unattended ad removal. Residual ad time was not measured, and application cuts were not automatically approved. No updated package is claimed by this record. Frozen 0.5.5 packages, source archives and prior measurements retain their original identities and sponsor/product-only scope.
