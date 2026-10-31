# Windows 0.5.6 reviewed candidate

The portable app and full installer were rebuilt from `6bcfe6ce74dcb179e4a7d5704383d4470afef2da`. Both passed real CUDA speech recognition, native window and service lifecycle checks, batch and export workflows, and source identity checks. The installer also passed current-user installation, shortcut and registry verification, uninstall and preservation of the test libraries. Portable relocation preserved the library, recordings, transcript, cut decisions and listening state.

This build includes fast v10: remove genuine sponsor reads and separate podcast promotions, preserve fake ads, jokes and contextual recommendations within the show. Manual review remains required. The [complete-episode CUDA results](2026-10-31-current-policy-cuda.md) and [listening observations](2026-10-31-listening-corrections.md) remain separate from package validation.

| Check | Portable | Installer |
| --- | --- | --- |
| Frozen runtime, CUDA allocation and GPU VAD prerequisites | Pass | Pass |
| Actual large-v3 CUDA transcription of the cached 20-second sample | Pass: 4 segments | Pass: 4 segments |
| Native window, owned server startup and clean shutdown | Pass | Pass |
| Imported-transcript batch workflows and three-item export | Pass | Pass |
| Real FFmpeg removal of a known four-second interval | Pass: 8.00-second output | Pass: 8.00-second output |
| Default library behavior and retained user data | Pass, including relocation | Pass, including uninstall |
| File hashes, dependency bindings and notices | Pass | Pass |
| Packaged 27B classifier generation and speech/classifier handoff | Deferred | Deferred |

The checks made two short speech-recognition jobs and zero classifier or paid API calls. No weights were downloaded. Available GPU memory was 17,879 MiB before the portable checks and 17,832 MiB before the installed checks, above the conservative 10,240 MiB floor used for these short speech tests. The reviewed 27B classifier needs more free memory. Games remained active, and the owner chose to continue packaging. Classifier startup, generation, reviewed-Qwen defaults and classifier/speech sleep-wake handoff therefore remain unverified in these exact packages. No smaller classifier, reduced context, CPU inference or simulated hardware-rejection result was substituted.

Both packages ran with external Python and FFmpeg removed from PATH. CUDA float16 speech configuration and the CUDA voice-activity provider were verified, with CPU fallback disabled for voice activity inference. The test installer was removed afterward, all owned application servers stopped, and the temporary package workspace was removed. The owner's library was not used.

## Delivery artifacts

The files are in `dist/windows/candidate-0.5.6-v2`. The [machine-readable evidence](2026-10-31-windows-promo-candidate.json) and adjacent `SHA256SUMS.txt` contain the complete hashes.

| Artifact | Bytes |
| --- | ---: |
| Castwell-0.5.6-Windows-x64-Portable.zip | 1,963,034,549 |
| Castwell-0.5.6-Windows-x64-Setup.exe | 1,230,144,371 |
| Castwell-0.5.6-Windows-x64-manifest.json | 397,011 |
| Castwell-0.5.6-Source.zip | 6,331,293 |
| Castwell-0.5.6-Dependency-Sources.zip | 336,175,448 |

Independent archive verification checked all five delivery checksums, all 2,017 portable manifest files and ZIP CRCs, all 234 committed application source files, 46 dependency source inputs across nine reviewed groups, and 71 native binary bindings. Internal and external build manifests agree. The installed files were separately checked against the same manifest and bindings, including 22 supplemental notice files.

The application source ZIP contains the complete exact committed tree, with its Git revision and original blob bytes verified. Dependency binaries reuse the controlled source builds and pins from the preceding candidate; the current dependency archive includes the updated inventory and completed Microsoft developer-setup status. Matching application and dependency sources must accompany any binary distribution. Source collection and checksum verification do not independently certify legal compliance or ad-removal accuracy.

An earlier build from `f596aef` was interrupted after the owner clarified that contextual recommendations should remain. Its portable archive and partial installer are preserved privately and were not published. The final v10 build uses a separate workspace and output directory. Existing 0.5.5 artifacts were not changed.

These artifacts are unsigned and keep model weights separate. They are a reviewed candidate, with the remaining packaged-classifier check stated above. The package checks do not establish unattended ad-removal readiness, full-episode audible accuracy or a processing-speed result. A later documentation commit does not change the frozen application revision or these artifact hashes.
