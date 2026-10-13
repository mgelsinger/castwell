# Ad-removal system evaluation

The compact CUDA detector is connected to the source application. It is substantially faster than the released detector, but the fresh accuracy gate fails. Castwell must remain a reviewed workflow until those errors are resolved and independently validated.

## Fresh results

This run used the same pinned Qwen3.8 27B Q4_K_M model, native llama.cpp with all 66 layers on CUDA, a complete contextual intent sweep, focused scene boundaries, and separate endpoint review. Reference word labels were frozen before these predictions. There were 44 model requests across the entire run, with no paid requests and no prediction cache hits. The [aggregate evidence](2026-10-12-ad-removal-system.json) records source/model hashes, actual token usage, conserved word counts, and the failed gate.

| Recording scope | Fresh classification | Commercial words missed | Protected words proposed for removal |
| --- | ---: | ---: | ---: |
| Stuff They Don't Want You to Know, July 30, complete 75:13 recording | 7:19 | 133 entries / 42.60 word-seconds | 52 words / 11.96 word-seconds |
| Stuff You Should Know, October 7, previously unused final 5:56 | 0:10 | 0 | 0 |
| Skeptoid, October 6, previously unused final 7:40 | 1:40 | 13 words / 4.62 word-seconds | 0 |

The July recording's CUDA large-v3 transcription took a separate 4:44. Classification request time across all three cases was 9:06; execution including local overhead was 9:10. Actual usage was 132,784 prompt tokens and 19,214 completion tokens. These timings do not include download, model loading, human review, or audio export.

All proposed cuts remained unapproved. The July episode's 167 policy-ambiguous trailer words and all declared unknown audio gaps remained untouched. Its first five minutes were previously consumed; all reported errors occur in the unused remainder. The SYSK tail contains no reference-labeled commercial speech, so its clean result tests preservation rather than ad recall. These are provisional transcript/context labels, with no independent human listening verification. Word-seconds measure summed ASR word durations, not whole advertisement elapsed time; zero-duration word entries are counted separately in the JSON.

## What needs repair

- **Scene membership:** a persuasive or joking introduction can be rejected when its completing offer is outside the requested output targets, even though that offer is in context. A nomination limited to six neighboring units can also omit an early setup entirely.
- **Internal transitions:** one broad commercial range can span several reads, a break announcement, and a split publisher bumper. Checking only that range's first and last two units cannot protect an editorial transition inside it.
- **Self-promotion:** an invitation to book the host's professional speaking service was partly treated as ordinary availability.
- **Speech coverage:** CUDA voice activity checks find possible activity in several timestamp gaps even with large-v3. A gap is not proven silence, and correct classification of supplied text cannot certify missing text.

One bounded diagnostic compared a general boundary-context repair with official Jev on identical target/context records. It reused 27 unchanged intent captures openly and capped each provider at twelve requests. Both stopped without retries when their strict output contracts failed: Qwen after seven requests and Jev after eight. The planned unpaid-performance controls were not reached. This was a consumed repair experiment, not another fresh acceptance test.

On the six records completed by both providers, covering 240 units and 2,189 words, Qwen proposed 71.40 seconds of protected word duration and missed four zero-duration commercial entries. Jev proposed 4.74 seconds of protected word duration and missed 13 commercial words, representing 3.04 seconds. These partial results do not establish a complete model ranking or justify a hybrid. The repair was not promoted. Jev's eight attempts cost **$0.004599756**, with no retries. Local request execution took 101.00 seconds; Jev's took 10.77 seconds. These times exclude the reused historical intent work and cannot be presented as full-episode timings. The [aggregate evidence](2026-10-12-ad-removal-system.json) preserves both failures separately from semantic errors.

## Setup tradeoffs

| Setup | Strength | Limitation | Current decision |
| --- | --- | --- | --- |
| Local Qwen3.8 + compact scenes + endpoint review | No recurring provider charge; full transcript sweep; measured seven-minute full-recording classification on the tested GPU | Fresh scene and boundary errors; 27B weights require substantial GPU memory | Connected, manual approval by default |
| Local Qwen3.5 | Earlier matched small comparison recovered the labeled ads | That comparison cut more protected speech; it has not passed this new full-recording protocol | Retain as a comparison, do not replace Qwen based on unrelated general rankings |
| Official Jev | Typed per-question decisions and probabilities; fast API calls; can decompose scene role from offer relationship | Text only; missed real speech and cut protected speech in the bounded diagnostic; one answer contradicted its reported probabilities | Bounded comparison failed; retain manual decisions |
| Qwen followed by Jev | Can ask a second model a narrowly defined unresolved question with full surrounding context | Agreement is not independent truth; union can add false cuts, while intersection can leave ads | No automatic hybrid promotion without measured improvement on the same references |
| Local Kev | A distinct locally hosted classification model, avoiding a remote API | It is not a local Jev release; earlier small tests did not establish superior ad accuracy | No broad rerun or model grid |
| Fine-tuned local model | Could learn this domain's scene membership and transitions | Requires trustworthy labeled audio boundaries, policy decisions, held-out recordings, and new CUDA validation | Build verified scene-role data before deciding whether tuning improves the frozen holdout |

The [earlier standardized comparison](../standardized-model-comparison-2026-10-08.md) used its own fixed small corpus. Its Qwen3.5, Qwen3.8, Jev, and Kev results can be compared within that corpus; they must not be ranked against this run as if the inputs and policies were identical. Jev accepts text rather than audio, and its current model customization is through state and questions, not per-account fine-tuning. [TypeSafe model reference](https://docs.typesafe.ai/models).

## Application and export checks

The actual source application and Jobs pipeline reproduced the frozen detector requests and cut intervals from exact recorded responses, with zero new inference. Ordinary mixed batches kept Remove episodes in review and made Keep originals ready. Queued detector/model/approval preferences stayed fixed. Global unresolved decisions blocked automatic approval and preferred cleaned exports. The later v2 approval policy also keeps a confident editorial boundary veto unresolved when the intent pass identified commercial speech; endpoint trimming cannot erase that conflict. A missing or failed checked classifier cannot become a successful empty no-ad result.

One real full-recording export used thirteen corrected reference cuts as an explicitly manual test fixture. It removed 701.58 seconds, selected all 1,985 reference commercial word entries, preserved all 10,345 protected and 167 ambiguous entries, and selected no declared unknown gap. Complete PCM decoding produced 3,811.54 seconds of audio. Original bytes were unchanged. This verifies rendering and word-interval handling; it is not a successful detector output or a listening-certified ad-free episode. Coverage remains unresolved and that export stays a manual preview.

The browser check exercises desktop/mobile operation, mixed batches, real fixture audio exports, and explicit coverage acknowledgement. An acknowledgement records the user's report of listening, retains all gap and model evidence, and requires a separate render. Changed originals or transcripts invalidate it. Installation and interface checks remain separate from ad-removal acceptance.

Episode processing and retry admission now exclude concurrent speech-model preparation in both directions. Explicit preparation shares the speech CUDA lock, clears idle speech weights, and observes only the owned classifier's sleep state. Host downloads and rendering remain available. These admission and resource checks are software behavior tests, with no extra paid requests or model accuracy claims.

No CPU neural inference fallback is provided. Local Whisper, voice activity detection, and classifier inference require NVIDIA CUDA. An actual GPU lifecycle check verified that the owned llama.cpp model unloaded while idle, large-v3 transcribed a 20-second speech crop in 6.34 seconds, and the classifier woke with all 66 layers on GPU. Total check time was 22.98 seconds with two local synthetic requests and no paid calls. CUDA voice activity detection disabled CPU fallback. Both owned processes stopped afterward. This verifies GPU resource sharing, not ad accuracy. Ordinary decoding, rendering and file handling use normal host resources.

## Work required before automatic release

The next accuracy step is an audio-verified scene-role dataset, rather than another broad model grid. Label complete commercial scenes, persuasive setups, internal editorial transitions, publisher credits, host self-promotion, and unpaid performances while listening to the original audio. Include paired cases that use similar advertising vocabulary with opposite intent. Resolve the policy for free trailers explicitly. Keep uncertain or untranscribed passages outside the scored truth until verified.

Split training, development, and acceptance data by whole episode and preferably show before tuning. Keep the reserved October 8 remainder untouched until the development gate passes. It is an unused portion of a previously consumed episode, so a new complete recording must be reserved before candidate design and predictions to establish an episode-level holdout. Compare the current local setup, one justified tuned alternative, and any narrowly scoped Jev confirmation on the same frozen inputs. Require zero missed commercial words and zero protected words cut in each scored recording, plus listened boundary and coverage verification. A finite passing test supports that defined test scope; it cannot promise that every future podcast will be error free.
