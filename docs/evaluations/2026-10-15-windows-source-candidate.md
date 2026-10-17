# Castwell 0.5.5 dependency and Windows validation results

**The dependency source collection is complete for the reviewed inventory. Both rebuilt Windows candidates passed their actual CUDA and installation checks. Public release remains held for Microsoft redistribution eligibility and independent audible ad-removal acceptance.**

The binaries and application source ZIP are frozen at [`3d72dcac3929d9f77f220db1ac43565334660848`](https://github.com/mgelsinger/castwell/tree/3d72dcac3929d9f77f220db1ac43565334660848). This report was added after packaging; a later documentation commit is not the binary source revision. The [machine-readable evidence](2026-10-15-windows-source-candidate.json) records exact artifact hashes, requests, cleanup and limitations.

## Completed work

Replaced the previous media binaries with controlled FFmpeg 8.1.2 audio tools and a custom `PyAV 16.1.0+castwell1` wheel using the same source-pinned shared libraries. Collected the exact source archives, version patch, build recipes, toolchain sources, checksums and license notices. Added the reviewed certifi/Eigen sources and missing runtime notices. The separate source archive contains 46 locked artifacts across nine reviewed component groups, with 71 bindings checked against the actual packaged binaries.

Pinned Microsoft's runtime to its verified official 14.44.35211 files and isolated packaging from unrelated application folders. Both packages verified every one of their 2,013 recorded files and all 22 supplemental notices. The project source ZIP contains all 227 tracked files as exact Git blob bytes. Archive CRC checks passed for the portable app and both source ZIPs.

| Check | Result | What it establishes |
| --- | --- | --- |
| Portable app and installed app | Passed on NVIDIA RTX 3090 Ti, 24 GB VRAM | Real packaged runtime operation on the tested machine |
| Local classifier | Two short requests per package; all 66 layers on CUDA; 32,768-token context | Format/label checks, full GPU offload, sleep/wake and speech handoff |
| Speech preparation and transcription | Cached large-v3, CUDA VAD with CPU fallback disabled; 20-second speech handoff per package | Packaged speech path works with the cached model |
| Native window and app workflow | Startup/shutdown, known-cut render, local RSS and ZIP export passed | Desktop operation and basic workflow |
| Portable relocation | Audio, transcript, cut state and listening position retained | Moving the test portable app preserved its test data |
| Current-user install/uninstall | Shortcut, registry and uninstall passed | Test libraries survived uninstall; owned test services stopped |
| Seven audio export formats and eight input formats | Passed; WAV cuts sample-exact | Controlled media compatibility, including audio extraction from MP4 video |
| Full-episode decoder comparison | Both complete recordings byte-identical through both PyAV and the standalone batched FFmpeg path | The replacement preserved transcription input for these recordings |
| Source tests | 1,106 tests and 193 subtests passed; two Windows symlink skips | Software behavior; one Starlette deprecation warning retained |
| Final packaging regression checks | 29 tests and 34 subtests passed | Guide-link correction and dependency/source checks |
| GitHub checks at the frozen revision | Python 3.10/3.12, browser flows and hour-long batch workflow passed | Hosted source checks, separate from local CUDA package validation |

There were **four short local classifier requests, zero paid API calls and zero model downloads** in final package validation. No full-episode inference was repeated for this dependency work. All neural inference used CUDA; FFmpeg performs conventional audio decoding and rendering. Speech and classifier weights remain separate from these unsigned candidates.

The package tests used temporary fixture libraries. They did not access the user's actual library. Owned application processes stopped and the temporary package workspace was removed. Private diagnostic fixtures were retained. GPU-unavailable rejection used a visibility mask on the NVIDIA machine, not a separate unsupported computer.

Two preliminary attempts failed before any application or model process started: the source ZIP had Windows newline conversion, and a bundled guide link pointed outside the app. The source ZIP was regenerated with exact Git bytes; the link handling was corrected and both packages rebuilt. Their failure records remain in the evidence. An independent review reconciled the final reports, native logs, request counts and artifact identities without more inference.

Detailed compatibility records: [media formats](2026-10-15-controlled-media.json), [PyAV inputs](2026-10-15-controlled-pyav-pcm.json), [batched FFmpeg inputs](2026-10-15-controlled-batch-pcm.json). Frozen source checks: [branch run](https://github.com/mgelsinger/castwell/actions/runs/38841223299) and [pull-request run](https://github.com/mgelsinger/castwell/actions/runs/38841228947).

## Performance and ad-removal status

The detector settings are unchanged. The app retains the current two-pass profile and mandatory review. These historical October 14 timings were not rerun in the rebuilt packages:

| Setup | July processing per audio hour | August processing per audio hour | Status |
| --- | ---: | ---: | --- |
| Current two-pass | 4:37.36 | 4:01.80 | Enabled with review |
| One-pass experiment | 3:50.62 | 3:37.67 | Experimental; not enabled |
| Reduced reasoning | Failed sponsor-omission guard | Not attempted | Rejected |

The successful setups produced identical output within each recording. Their provisional transcript scores selected zero protected word-seconds and left 1.31/3.13 commercial word-seconds per episode. These values do not measure audible ad remainder or audible show loss. The July timing comparison was affected by background GPU activity; August saved about 10% in one matched pair. Both recordings were reused development material. See the [full timing and tolerance matrix](2026-10-14-tolerance-comparison.md).

## Prepared local files

| File | Size, decimal | Purpose |
| --- | ---: | --- |
| `Castwell-0.5.5-Windows-x64-Portable.zip` | 1.963 GB | Extract and run the complete app folder |
| `Castwell-0.5.5-Windows-x64-Setup.exe` | 1.230 GB | Current-user installation |
| `Castwell-0.5.5-Dependency-Sources.zip` | 336.18 MB | Exact reviewed dependency sources, recipes and notices |
| `Castwell-0.5.5-Source.zip` | 6.30 MB | Exact Castwell source revision used for these binaries |

The local `dist/windows/candidate-0.5.5` folder also contains the build manifest, validation report, delivery index and `SHA256SUMS.txt`. These binaries and source archives have not been uploaded as a public release. The source code and this evidence are available in the candidate branch. Old downloads are unchanged and do not acquire the new source coverage retroactively.

## What still needs human involvement

1. **Establish Microsoft redistribution eligibility.** The official runtime's provenance is verified, but downloading it is not itself a distributor grant. A free Visual Studio Community 2022 route has been prepared for this open-source project, subject to its [license terms](https://visualstudio.microsoft.com/license-terms/vs2022-ga-community/) and [redistribution list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution). Its signed installer was downloaded and verified but not launched. This session is not an administrator, and Microsoft's [installer policy](https://learn.microsoft.com/en-us/visualstudio/install/configure-policies-for-enterprise-deployments?view=visualstudio) does not allow standard users to perform unattended setup. Complete the qualifying setup/use, retain that evidence, and rebuild/recheck any changed release inputs. Installation alone is not a blanket legal certification. Castwell's own installer does not require this developer setup for users.
2. **Complete independent listening acceptance.** Listen to the original and proposed output around all commercial boundaries, preserved parody and unknown speech gaps, then test a fresh complete episode against independent references. The existing short listening packet is ready. This must establish the agreed audible residual-ad limit and show preservation; software tests and transcript proxies cannot substitute for it. The final one-pass versus two-pass choice remains open.

Once those conditions are satisfied, publish and retain the exact matching application source, dependency source, binaries and checksums together. Source collection and package operation are complete within their stated scope; unattended ad-removal acceptance and production go-live are not confirmed.
