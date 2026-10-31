# Five-second tolerance and CUDA timing comparison

Measured October 14, 2026 on an NVIDIA RTX 3090 Ti with 24 GB VRAM and 128 GB system RAM. The [machine-readable results](2026-10-14-tolerance-comparison.json) retain identities, request counts, stages, threshold checks and limitations.

**Both successful setups met the five-minute-per-audio-hour goal on both recordings and produced byte-identical audio within each episode. Allowing a few seconds of show loss bought no additional benefit in this experiment.** Keep the zero-show-loss target. The faster one-pass setup is promising for further validation; the rebuilt 0.5.4 app retains the existing two-pass setup and mandatory review while the owner chooses the final tradeoff.

These are two reused development recordings, not independent acceptance episodes. The accuracy figures below are sums of provisional transcript word durations. They do not establish elapsed audible ad remainder or audible show loss, and neither setup has passed unattended production acceptance.

## Measured results

| Setup | Recording length | Full processing time | Processing per audio hour | Commercial word-duration remainder per episode | Remainder per audio hour | Protected word-duration selected |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Current two-pass, July | 75:13 | 5:47.71 | 4:37.36 | 1.31 s | 1.04 s | 0 s |
| Faster one-pass, July | 75:13 | 4:49.11 | 3:50.62 | 1.31 s | 1.04 s | 0 s |
| Current two-pass, August | 79:55 | 5:22.06 | 4:01.80 | 3.13 s | 2.35 s | 0 s |
| Faster one-pass, August | 79:55 | 4:49.92 | 3:37.67 | 3.13 s | 2.35 s | 0 s |
| Reduced reasoning, July | 75:13 | Stopped after 2:49.07 | No successful timing | No rendered result | Unknown | Unknown |

The five-minute goal scales with source duration. A 79:55 episode therefore has a processing budget of about 6:40; a total time over five minutes does not by itself fail the per-hour goal. Model loading, fresh CUDA transcription, classification, waveform/database preparation, saved analysis and a rendered preview are included. Initial downloads, preflight hashing/probing, user-interface/queue time and post-output cleanup are excluded. No transcript or prediction cache was reused. Models started cold, but operating-system file caches were not flushed.

The game was still active for the first two July attempts and closed during the faster July run. That pair's 58.60-second difference is confounded by background load. Both August runs started at 0% observed GPU utilization, about 1.2 GiB occupied VRAM and 55 C, with the game absent; normal desktop applications remained open. This is a substantially better matched pair, not an isolated-machine timing study.

On August, one-pass processing saved **32.14 seconds per episode, or 24.13 seconds per audio hour, about 10%**. Whole-episode model requests took 169.61 and 169.50 seconds with identical input/output token counts. The current setup's additional boundary request took 30.76 seconds. This supports attributing most of that observed difference to the skipped pass. There is one run per setup per episode, not a repeat-run timing distribution or proof of performance on other GPUs.

## What changed between setups

| Setup | Behavior | Benefit | Limitation |
| --- | --- | --- | --- |
| Current two-pass (S) | 6,144-token whole-episode reasoning budget, followed by a focused 1,024-token boundary review | Retains the existing extra check; met the speed goal on both recordings | Extra pass cost about 31 seconds on August and changed no output in these two cases |
| Faster one-pass (H) | Same whole-episode reasoning; original-word boundaries without a separate model boundary request | Same rendered audio here with fewer requests and lower measured latency | No focused boundary judgment; mixed or uncertain units stay unresolved; evidence is limited to consumed recordings |
| Reduced reasoning (F) | 2,048-token whole-episode reasoning, with the usual boundary pass planned | Shorter first model request | Omitted a sponsor block near source timestamp 5:35; the omission safeguard rejected it before any output was rendered |

F was stopped after that single failure and was not tried on August. Its short failed attempt is not a speed success. No retry-until-pass selection was used. H was the bounded fallback experiment after F failed, not a promoted application setting.

Every setup used the same pinned Qwen3.8-27B Q4_K_M file, native llama.cpp with a 32,768-token context and all 66 layers on CUDA, large-v3 CUDA float16 speech recognition at batch size eight, and CUDA speech activity detection without CPU fallback. The gap split remained 0.8 seconds and no edge padding was added. The target remained sponsor/product reads; free podcast promos, editorial speech and unpaid parody remained protected.

There were **five full attempts, four completed outputs and seven local classifier requests, with zero paid API calls**. The private experimental adapter changed only request reasoning or the presence of the boundary request. Application inference code was frozen throughout, and existing user libraries and approvals were not modified.

## Tolerance matrix

The ad allowance is five seconds per source hour. Show-loss allowances are per episode. One, three and five seconds are exploratory comparison columns, not a selected policy.

| Setup and recordings | 0 s show loss | 1 s show loss | 3 s show loss | 5 s show loss | Actual audible acceptance |
| --- | --- | --- | --- | --- | --- |
| Current two-pass, both episodes | Within word-proxy limits | Within word-proxy limits | Within word-proxy limits | Within word-proxy limits | Not verified |
| Faster one-pass, both episodes | Within word-proxy limits | Within word-proxy limits | Within word-proxy limits | Within word-proxy limits | Not verified |
| Reduced reasoning, July | Failed run | Failed run | Failed run | Failed run | No output |

There was no measured show-word loss to exchange for further speed. These results support keeping the zero-loss goal rather than relaxing it merely to enable the faster experiment. They do not guarantee zero audible loss. A completely missed sponsor read remains unacceptable at every tolerance; the recordings do not have an independently listened, complete sponsor-read inventory, so whole-read coverage cannot be certified from this table.

## Real ads, parody and output validation

Both August outputs preserved all 193 entries in the three frozen reference-labeled parody controls, including the rutabaga material. Those controls contain 60.72 seconds of provisional word duration. Both setups selected zero protected words across the full references. Their remaining reference discrepancies were 14 commercial entries totaling 1.31 word-seconds in July and 15 entries totaling 3.13 word-seconds in August. Payment status and parody intent were not independently verified by a listener in this experiment.

The July S/H outputs match each other and the prior audited July v6 output byte for byte. Their ordered cut intervals, source word text/times, saved word decisions and boundary ownership also match, so the existing July audit and 18-window listening packet remain applicable. That carries forward evidence; it does not constitute a new listen or decode.

August S/H also produced identical rendered bytes. The August source and unique output were fully decoded after timing, and both cut maps were audited. Each recording has 15 cuts and 30 valid endpoints. Every positive-duration source word labeled commercial is covered: 1,955 in July and 2,092 in August. No noncommercial source word intersects a cut and no source words remain unresolved. These source labels are model decisions, not independent ground truth.

Between commercial-labeled source words, the audit identifies **nine retained unclassified gaps totaling 12.73 seconds in July and eleven totaling 12.22 seconds in August**. These totals do not measure all unclassified audio in either episode. The gaps may contain pauses, music or unrecognized speech; they are not automatically counted as correct silence or as remaining ads. Both outputs match the declared edit timeline after full decoding. The fully decoded originals extend approximately 26.12 ms beyond their declared durations, and the renderer caps edits at the declared timeline. July's excess tail was previously shown to be nonzero; the current audit does not establish the audible significance of either tail. Duration and hash checks do not prove acoustic cut quality.

The new offline comparator has 29 passing focused tests, including mismatched cohorts, failed or incomplete timing, unknown telemetry/downloads, rendered-versus-proposed scores, interval arithmetic, whole missed reads and explicit uncertainty. An independent review checked its safeguards and the public aggregate. The complete 0.5.4 source suite passed 1,066 tests and 173 subtests in 70.81 seconds, with two Windows symlink tests skipped; JavaScript syntax and whitespace checks also passed. These are software tests, not additional model evaluations. Neither tests nor reference reuse create listening-verified acceptance.

## Rebuilt app and remaining decision

Version 0.5.4 includes the current **Hear start / Hear end** controls, AGPL-3.0-only project terms, the source inventory and this comparison. Its detector retains the current two-pass setup. The one-pass adapter remains an experiment and is not selectable in these packages. Exact artifact checksums and the source revision are recorded with the rebuilt portable app and installer; package validation is separate from this source-processing benchmark.

The evidence supports keeping the five-minute processing goal, the five-second residual-ad allowance and the zero-show-loss target. The one-pass candidate is worth the next independent audio check because it saved time without changing either measured output. Before unattended go-live, validate the audible boundaries and unknown gaps against a new complete recording with independent references. Public binary distribution also retains the [third-party corresponding-source limitations](../third-party-sources.md). The final tradeoff remains the owner's decision.
