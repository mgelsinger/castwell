# Archived local ad-read comparison, October 4, 2026

**Archive notice, October 8, 2026:** this page preserves the earlier Qwen 2.5 7B results. See the [October 8 readiness report](../readiness-2026-10-08.md) for the dense v8 response replay, its remaining errors, completed v7/v6 and earlier results, and the four-show protocol. Both the original 27 cases and the subsequent 33-case challenge are now development/regression material; their original `eval` labels do not make later runs held out.

The native llama.cpp setup works on the RTX 3090 Ti. The tested Qwen 2.5 7B classifier is **not ready for unattended cuts**: it found more commercial speech and also approved more editorial speech for removal. No audio or library settings were changed by this comparison, and no paid API requests were made.

## Held-out results

The frozen challenge contains 18 evaluation cases from six fictional podcast/host groups. Sixteen have authored reference labels totaling 144 commercial seconds and 273 editorial seconds. Two ambiguous cases, totaling 50 seconds, are excluded from accuracy metrics and reported separately. These are synthetic transcripts with authored times, not measured recordings.

The table scores the cuts that each existing pipeline would approve at the fixed 0.90 threshold. It does not mean that audio was actually deleted.

| Measure | Local rules | Qwen 2.5 7B via llama.cpp |
| --- | ---: | ---: |
| Editorial seconds wrongly approved | 45 | 53 |
| Commercial seconds missed by approved cuts | 84 / 144 | 0 / 144 |
| Cases with exactly correct approved intervals | 6 / 16 | 11 / 16 |
| Mean error across matched start/end boundaries | 2.5 seconds | 0.9 seconds |
| Unmatched predicted / reference spans | 2 / 5 | 3 / 0 |
| Cases containing unapproved suggestions | 9 / 18 | 5 / 18 |
| Required-review cases actually flagged | 2 / 4 | 0 / 4 |
| Request or validation failures | 0 / 18 | 0 / 18 |

Boundary means apply only to matched spans; they do not include false ads or wholly missed ads. The separate unmatched counts and duration errors capture those mistakes. Fewer review flags are not an improvement here: the model approved mixed editorial/commercial segments with high confidence.

When scoring **all proposals**, including suggestions left for manual review, both pipelines selected 83 editorial seconds. Rules missed 40 commercial seconds; the contextual pipeline missed none. The contextual pipeline includes Castwell's existing heuristic fallback suggestions, so these proposal numbers are not a pure model-only classifier score.

Both ambiguous cases produced no cuts and no review signal in either pipeline. This avoided unsupported automatic removal in those two examples, but neither pipeline explicitly recognized their uncertainty.

## What worked and what failed

- **Humorous paid ads:** Qwen selected both complete 20-second paid reads correctly. Rules completely missed one in their approved output.
- **Unpaid parody:** Qwen approved 10 seconds of one fictional sketch and 15 seconds of another. Rules approved 15 seconds in each. The requested distinction between a joke and a real paid read remains unresolved.
- **Quoted editorial speech:** Qwen approved 10 seconds of an advertisement being quoted in an editorial passage. Its other quoted example stayed unapproved.
- **Delayed return markers:** Qwen kept the intervening editorial sentences in both cases. Rules would remove 15 editorial seconds across those cases.
- **Mixed segments without word timing:** Qwen approved 18 editorial seconds across two cases that required review. A model cannot infer precise internal timestamps from the available segment-level timing, and its confidence did not reliably enforce that limitation.
- **Self-promotion and ordinary brand mentions:** Qwen found both paid self-promotions and approved no cuts in the two ordinary brand discussions.

This is evidence for using the local model as a source of reviewable suggestions. It does not establish safe automatic removal or accuracy on real podcasts. Jev has not been evaluated live, so there is no measured Jev-versus-Qwen result.

## Execution and provenance

The development run covered nine separate cases before the evaluation configuration was frozen. Its approved output removed 20 editorial seconds with rules and 41 with Qwen. We kept the existing prompt and settings unchanged, recorded the candidate as evaluation-only, and ran the held-out split once. There was no tuning or rerun based on held-out predictions.

- Native runtime: official llama.cpp **b11146**, commit `7fe450e19`, Windows CUDA 12.4 x64 build and matching CUDA runtime.
- Model: official **Qwen2.5-7B-Instruct Q4_K_M**, pinned revision `bb5d59e06d9551d752d08b292a50eb208b07ab1f`, two shards totaling 4,683,073,632 bytes.
- Hardware: NVIDIA GeForce RTX 3090 Ti. The running server was observed as a CUDA compute process; `--n-gpu-layers all` was used. Windows did not report per-process VRAM usage.
- Server: loopback port 8081, model alias `castwell-local`, 8192-token context, eight CPU threads, one parallel slot.
- Classifier: existing Castwell prompt, temperature 0, 18,000-character windows, 12 context segments per side, 180-second request timeout, 0.90 approval threshold.
- Timing: all 18 local-model requests completed in **6.35 seconds** combined, with a **0.432-second median**. These short transcript cases used an already loaded model with prompt caching. This excludes download, transcription, model startup, and audio rendering; it is not a full-episode processing benchmark.
- Validation: **193 tests and 116 subtests passed**, including real loopback redirect rejection and mocked Jev contract/cost guards. One dependency deprecation warning was reported.

Both model shards and both native release archives passed SHA256 verification. Exact source URLs, hashes, runtime settings, and source-code hashes are in the frozen configuration below. Result timestamps use UTC, which was October 5 during this October 4 evening local run. The Git revision in the raw results is the base revision with `git_dirty: true`; source hashes identify the evaluated working files.

| Artifact | Contents |
| --- | --- |
| [Frozen configuration](qwen-7b-configuration.json) | Model/runtime provenance and configuration recorded before the held-out run |
| [Development output](qwen-7b-development.json) | All nine cases for both pipelines, with predictions, reasons, and metrics |
| [Evaluation output](qwen-7b-evaluation.json) | All 18 held-out cases for both pipelines, including uncertainty and failures |
| [Challenge and methodology](../ad-read-evaluation.md) | Frozen dataset hash, metric definitions, and user-supplied ground-truth format |

Run a comparison from the project environment with llama.cpp already running:

```powershell
$env:CASTWELL_AI_WINDOW_CHARS='18000'
$env:CASTWELL_AI_CONTEXT_SEGMENTS='12'
$env:CASTWELL_AI_TIMEOUT='180'
.\.venv\Scripts\python.exe scripts/compare_detectors.py --backend heuristic --backend local-ai --split dev --model-label 'Qwen2.5-7B-Instruct Q4_K_M, llama.cpp b11146 CUDA12.4, RTX3090Ti' --output .local/development-rerun.json
```

The checked-in evaluation split has now been inspected. A future tuned candidate needs a fresh held-out set, preferably real clips with independently reviewed boundaries and disjoint hosts/podcasts. Keep the checked-in outputs intact when recording a later run.

## Local setup and optional paid work

The project environment is `.venv`. Native binaries are in `.local/llama.cpp`, model weights in `.local/models`, and runtime logs in `.local/logs`. These local resources are ignored by Git. The server was left running after evaluation; after stopping it or rebooting, restart with `scripts/start_local_ai.ps1 -Background`. The launcher prints the process ID and log paths, and refuses to replace an existing listener.

The evaluation harness never updates the application's detector settings or enables automatic removal. For interactive use, configure the loopback endpoint and alias in Settings and enable **Review every suggestion**. Listen around proposed cuts before exporting.

The Jev adapter is implemented and its typed response handling is tested with mocks. Live Jev testing would require paid API access. TypeSafe lists `jev-1.13.0` at $0.042 per million input tokens, with output tokens free, as checked on the run date. No such request was made. See [TypeSafe's current pricing](https://docs.typesafe.ai/models) and the [explicit opt-in requirements](../ad-read-evaluation.md#optional-jev-comparison-and-cost).
