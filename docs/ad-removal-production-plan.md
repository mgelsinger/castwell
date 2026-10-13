# Deliver the specified ad-removal workflow

The release requirement is batch podcast preparation with accurate removal of real advertisements, preservation of unpaid parody and program speech, a per-podcast Remove ads or Keep original choice, and working CUDA-only portable and installer applications. Library features alone do not satisfy this requirement.

## Current evidence

The released detector takes 106:34 to classify a 79:55 recording. A compact scene detector with separate endpoint review is now connected to the application and CUDA launchers. It takes 7:19 to classify a different complete 75:13 recording. CUDA large-v3 transcription takes a separate 4:44. The speed problem is substantially improved; accuracy is still insufficient for unattended removal.

The frozen fresh test consists of one complete Stuff They Don't Want You to Know recording and previously unused tails from Stuff You Should Know and Skeptoid. It used 44 actual model requests in total, with references frozen before inference. The complete recording misses 133 commercial word entries, representing 42.60 seconds of word duration, and proposes removing 52 protected words, representing 11.96 seconds. The Skeptoid tail misses 13 commercial words, representing 4.62 seconds; the SYSK tail has no scored word errors. All proposals remain unapproved. Free other-podcast trailers with unresolved policy and audio without verified text are reported separately.

Independent verification confirms the request identities, complete unit ownership, original word mappings, actual token usage, and error arithmetic. A previously consumed episode passed an endpoint repair replay, but that replay did not predict success on the fresh test. It must not be presented as independent validation.

The remaining faults concern scene ownership: a persuasive setup can be excluded because its later offer is outside the output targets, and an overly broad range can absorb an editorial break announcement or publisher credit inside an advertising block. A request being complete and well-formed does not make those decisions correct.

The cached rutabaga sketch has provisional parody labels from transcripts and publisher context, with no independent listening or payment verification. Earlier preservation of that sketch is consumed development evidence. The latest bounded repair never reached its parody controls. Listening verification of the full sketch and neighboring real read is still required.

A private labeling packet now identifies eight contextual windows in three cached originals, totaling 15:18 of first-pass listening before boundary replay and annotation. It includes the complete rutabaga setup/sketch and neighboring real read, humorous sales setups, protected internal transitions, and the host's booking-service offer. It preserves source hashes and provisional labels, includes an unfilled listener template, and marks every item as consumed development material. Preparing that packet adds no verified labels by itself.

## Implementation sequence

1. Repair scene ownership before relying on endpoint trimming. Evaluate persuasive introductions against offers anywhere in the supplied context, including context-only units. Check transitions within broad ranges as well as their outer endpoints. Preserve the original word timestamps and complete decision audit.
2. Build audio-verified scene-role labels for complete paid reads, humorous introductions, unpaid parody, ordinary product discussion, internal transitions, publisher credits, and self-promotion. A bounded context repair has now failed with both Qwen and Jev; neither reached its planned parody controls. Preserve those failures and stop broad prompt or model grids. Use paired examples with similar vocabulary but opposite intent to test the actual classification problem.
3. Split the verified data by whole episode and preferably show into training, development, and acceptance sets before deciding whether to tune a local model. Keep the unused October 8 remainder untouched until a candidate passes the development gate; that remainder belongs to a previously consumed episode and is not a pristine whole-episode holdout. Reserve a new complete recording before designing or evaluating the next candidate for an episode-level acceptance claim. Compare only justified alternatives on the same frozen inputs and report reused predictions separately. Preserve failed attempts and avoid changing reference labels to fit a model.
4. Keep the connected detector behind manual approval until accuracy acceptance passes. Preserve per-podcast Remove/Keep choices and batch operations. Snapshot the detector and model settings for queued jobs and retries. Let Whisper and the owned llama.cpp server take turns using GPU memory, and verify that transition on actual CUDA hardware.
5. Make completion honest. An unresolved cut, incomplete detector audit, inadequate word alignment, or unverified transcription coverage must become an explicit batch exception. Preferred playback and automatic exports must retain the original for that episode. A partial reviewed preview must not be labeled completed ad removal.
6. Test the actual application on complete recordings, render its accepted cuts, verify duration and full audio decoding, and exercise mixed Remove/Keep batches. Build and test both portable and installed applications with the actual owned CUDA classifier, including relocation and uninstall.

## Acceptance measurements

- Zero missed commercial words and zero protected words cut in each frozen, scored acceptance recording. Report policy-ambiguous promotions and untranscribed audio separately rather than counting them as correct negatives.
- Preserve unpaid parody, including the rutabaga examples. Include humorous real advertisements and complete persuasive introductions.
- Measure fresh classification time separately from speech recognition, model loading, exports, and cached replay. The working performance target is under ten minutes of classification for an approximately eighty-minute episode on the tested RTX 3090 Ti.
- No CPU neural inference fallback. Missing CUDA support or insufficient GPU memory must produce a clear failure.
- No automatic completion or preferred cleaned export for unresolved episodes. Batch results must identify exceptions instead of silently claiming success.
- A complete model decision is not proof of audible accuracy. Listening verification of boundaries and untranscribed gaps remains a distinct acceptance requirement and must be reported honestly.

## Model and cost strategy

Use the installed Qwen3.8 model through native llama.cpp for the manual candidate. Earlier standardized tests did not show an accuracy benefit from combining Qwen with Jev for every transcript unit. The bounded context-repair diagnostic attempted seven local and eight official Jev requests before invalid outputs stopped both without retries. On their six common completed records, both also made semantic errors. Jev cost $0.004599756. Neither provider reached the parody controls, and neither repair was promoted. See the [system evaluation](evaluations/2026-10-12-ad-removal-system.md) for comparable measurements and limitations.

The current local models and installed CUDA runtime are sufficient for the manual workflow and subsequent development. Fine-tuning requires trustworthy labeled audio boundaries and a separate held-out test set first. More model size or self-reported confidence alone does not fix completion, boundary, or export behavior. Additional paid testing is not needed to establish the failures already observed.

This is a release plan, not a production acceptance certificate. Passing a consumed repair test still requires a later fresh recording check and listening verification. Portable and installer candidates must disclose any remaining accuracy failure instead of being labeled production ready.

The 0.5.2 portable and installer candidates have now passed their actual CUDA, export, startup, relocation, and installation checks. Their [validation record](evaluations/2026-10-13-windows-candidate.json) keeps those operational results separate from the failed accuracy gate. The next release blocker remains verified scene labels and a detector repair that passes independent episode acceptance.

The public recording evaluator now supports the actual composed scene/endpoint detector with complete private audits, frozen request settings, and explicit missing/failed decisions. Its [documented interval-reference format](ad-read-evaluation.md#real-recordings-with-partial-references) remains separate from the private word-reference format used in the fresh study. Offline dispatch and audit fixtures establish software behavior, not new model accuracy.
