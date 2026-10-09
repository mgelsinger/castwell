# Free local models on Windows

This setup runs speech recognition and classification on your own machine. Model downloads are public and free; no hosted inference, API key, Ollama, or Modal deployment is needed. The application and model servers bind to `127.0.0.1`.

The tested machine has Windows, Python 3.12.10, an RTX 3090 Ti with 24 GB VRAM, and 128 GB RAM. The retained Qwen3.5-27B dense profile uses an 8,192-token context and all GPU layers; the earlier 35B-A3B profile uses four CPU expert layers. Run only one model server on this GPU. Model weights, environments, binaries, logs, and private evaluation artifacts live under ignored `.local/` and are not committed.

## Application environment

Install Python 3.12, Git, and FFmpeg first. Both `ffmpeg` and `ffprobe` must be on `PATH`. From the repository root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[transcription]" "huggingface-hub>=0.34,<2"
```

Prepare the English speech model once. This downloads about 0.5 GB and does not process any podcast:

```powershell
.venv\Scripts\python.exe -c "from faster_whisper.utils import download_model; download_model('small.en', cache_dir='.local/models/whisper', use_auth_token=False)"
```

The app launcher uses this cache. Its default speech model is `small.en`; pass `-SpeechModel` to choose another installed model. The command resolves the publisher's current speech-model revision. For exact speech-model reproduction, download an explicit revision to a local directory and pass that directory with `-SpeechModel`; the tested small.en snapshot was `d1d751a5f8271d482d14ca55d9e2deeebbae577f`.

## Prepare native llama.cpp

The tested native runtime is [llama.cpp b11146](https://github.com/ggml-org/llama.cpp/releases/tag/b11146), build `7fe450e19`, with the portable CUDA 12.4 Windows runtime. A compatible NVIDIA driver is required. You do not need to compile Python bindings or install a system CUDA toolkit for this binary.

Download and verify both official archives before extraction:

```powershell
New-Item -ItemType Directory -Force .local\downloads, .local\llama.cpp | Out-Null
$assets = @(
    @{ Name = 'llama-b11146-bin-win-cuda-12.4-x64.zip'; SHA256 = '3c806a6ceccc3dae1c743ceb1a1fb2cce5b76f40bfbd4c6b7b8afb6ef45a5807' },
    @{ Name = 'cudart-llama-bin-win-cuda-12.4-x64.zip'; SHA256 = '8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6' }
)
foreach ($asset in $assets) {
    $archive = Join-Path '.local\downloads' $asset.Name
    Invoke-WebRequest -Uri "https://github.com/ggml-org/llama.cpp/releases/download/b11146/$($asset.Name)" -OutFile $archive
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $asset.SHA256) {
        throw "Checksum mismatch: $archive"
    }
    Expand-Archive -LiteralPath $archive -DestinationPath '.local\llama.cpp' -Force
}
```

After preparing the runtime, choose one model profile. The retained profile is [Qwen3.5-27B dense](#qwen35-27b-dense-v8-candidate), recommended only with manual review. The [completed local comparison](local-model-round2-2026-10-08.md) rejected both role-first alternatives; these setup commands and the production v8 policy remain unchanged. The smaller and earlier profiles remain below for reproducibility.

The later [compact standardized comparison](standardized-model-comparison-2026-10-08.md) uses eight shared cases with matched Qwen settings, both Kev sizes, official paid Jev and combinations. See that report for the measured tradeoffs, request counts and cost. Its evaluation-specific typed prompts and combination policy do not automatically replace the application's detector or the older CLI backends.

## Qwen3.5-27B dense v8 candidate

Known-recording v8 proposals remain 408.96 of 410.24 annotated ad-word seconds under original labels. The later correction of a 1.28-second editorial reference error gives 408.96 of 408.96, while 11.68 protected-word seconds remain proposed. This changes labels, not predictions. Actual fresh inference proposes all 32.72 primary ad-word seconds plus 16.30 protected-word seconds. See the [reference correction and fresh results](local-model-round2-2026-10-08.md); this setup is not an unattended-removal guarantee.

This candidate uses the third-party [Unsloth Qwen3.5-27B GGUF](https://huggingface.co/unsloth/Qwen3.5-27B-GGUF), based on [Qwen3.5-27B](https://huggingface.co/Qwen/Qwen3.5-27B). It downloads Q4_K_M directly, with no local requantization. The repository identifies the base model and Apache 2.0 license; the publisher's complete conversion process has not been independently reproduced. The [v8 candidate record](evaluations/2026-10-08-qwen35-dense-v8-candidate.json) fixes the selection policy, model and request profile. Its known-recording results replay the complete [v7 inference](evaluations/2026-10-08-real-qwen35-dense-v7.json), matching every request exactly and making zero new model calls. The [historical readiness report](readiness-2026-10-08.md) preserves the original results; these results do not support unattended removal. The narrow eight-case challenge completed with correct simulated approvals but 23.5 editorial seconds in review proposals. The consumed 33-case regression proposed all 306.8 commercial seconds plus 49 editorial seconds; its simulated approvals left 34 commercial seconds unselected in coarse mixed passages. Neither synthetic result establishes general recording accuracy.

| Pinned artifact | Identity |
| --- | --- |
| Repository revision | `3221f178a6b842d04f1fb42f1c413534adcc0a6a` |
| Filename | `Qwen3.5-27B-Q4_K_M.gguf` |
| Download size | 16,740,812,704 bytes, approximately 16.7 GB |
| SHA256 | `84b5f7f112156d63836a01a69dc3f11a6ba63b10a23b8ca7a7efaf52d5a2d806` |

Allow room for this file, the application, and the native runtime. From the project environment, download and verify it once:

```powershell
.venv\Scripts\python.exe scripts\setup_qwen35_dense_local.py
.venv\Scripts\python.exe scripts\setup_qwen35_dense_local.py --verify-only
```

The default directory is `.local/models/qwen3.5-27b`; `--directory` selects another location. `--verify-only` hashes existing files without network access or writes. Interrupted `.partial` files and corrupt existing models are preserved and refused, so inspect them or choose a new directory before retrying. A completed download records its provenance beside the model.

Stop the other model server first. To reproduce the frozen server defaults:

```powershell
.\scripts\start_qwen35_dense_local.ps1 -NoThinking -LogVerbosity 4 -Background
```

The launcher verifies both the model and b11146 server binary before loading, requires the matching runtime DLLs, and binds offline to `http://127.0.0.1:8081/v1` with alias `castwell-local`. It uses one slot, 8,192 context tokens, eight CPU threads, all GPU layers, batch 512 and microbatch 128. `-ValidateOnly` verifies files and prints arguments without starting a model. `-Background` starts hidden and writes unique logs and a PID under `.local/logs`; omit it for a foreground process. It does not stop an existing listener.

The observed RTX 3090 Ti load offloaded all 65 layers, using 15,272.77 MiB for CUDA weights, 512 MiB for KV state, 149.62 MiB for recurrent state and 32.5 MiB for compute; CPU-mapped weights occupied 682.03 MiB. Total observed GPU usage including desktop processes was 19,701 MiB, leaving 4,613 MiB free. These are one machine's startup measurements, not a guarantee for other hardware or inputs.

**Enable the client profile separately.** After starting Castwell, open **Settings > Contextual AI connection**, turn on **Use local Qwen3.5 reasoning**, and keep **Require my approval for every cut** enabled. The persisted `ai_reasoning` preference defaults to false and has no environment override. It is supported by the verified detector, not the legacy policy. Both evaluation CLIs require `--backend verified-ai --ai-reasoning` to select the same profile.

The v7 and v8 clients send temperature 1.0, top-p 0.95, top-k 20, min-p 0, presence penalty 1.5, repetition penalty 1.0 and seed 42. Each request explicitly enables thinking with a 1,024-token reasoning budget, `deepseek` reasoning format and a 4,096-token output cap. These request fields override the frozen server's nonthinking defaults. The new launcher's default, without `-NoThinking`, enables reasoning for clients that omit a choice; Castwell always sends its own choice. A server startup alone therefore does not enable reasoning in the app. Reports record the actual request profile, and another profile needs its own evaluation.

The dense model generated roughly 42 tokens per second during local probes. Ten focused development requests took 490.375 seconds versus approximately 200 seconds for the earlier 35B-A3B model. These selected-request timings exclude model preparation and full episode processing, and do not prove better classification. See the [readiness report](readiness-2026-10-08.md) for completed results and remaining limitations.

### Optional smaller Qwen3-14B profile

Download, verify, and start the official [Qwen3-14B GGUF](https://huggingface.co/Qwen/Qwen3-14B-GGUF) model:

```powershell
.venv\Scripts\python.exe scripts\local_ai.py --download --size 14b --cache-dir .local\models --backend native --server-binary .local\llama.cpp\llama-server.exe --threads 8
```

The helper pins revision `530227a7d994db8eca5ab5ced2fb692b614357fd` and verifies `Qwen3-14B-Q6_K.gguf`, 12,121,937,248 bytes, against SHA256 `ec1f1d1421d7636a23e17645cac6fda39ee4b5f1f11e42eb09238089705ef699`. It refuses a corrupt file before starting inference.

For later starts, use:

```powershell
.\scripts\start_local_ai.ps1
```

The endpoint is `http://127.0.0.1:8081/v1` and model alias is `castwell-local`. The launcher uses all GPU layers, one inference slot, 8,192 context tokens, a default 4,096-token output limit, and a 1,536-token reasoning budget. A client's explicit `max_tokens` overrides llama.cpp's default output limit, so clients must also set their own bound.

Thinking mode follows [Qwen's sampling guidance](https://huggingface.co/Qwen/Qwen3-14B-GGUF): temperature 0.6, top-p 0.95, top-k 20, min-p 0, and presence penalty 1.5. The model's native Jinja template and `deepseek` reasoning parser keep `message.reasoning_content` separate from schema-constrained `message.content`. With the default `ai_reasoning=false`, the verified detector explicitly requests nonthinking inference through `chat_template_kwargs.enable_thinking=false`, with temperature 0.7, top-p 0.8, seed 42 and a 4,096-token output cap. Its reason fields precede each label. This request overrides the server thinking default. `-NoThinking` also selects nonthinking defaults for other clients. Keep the setting off to reproduce these older runs; the optional Qwen3.5 reasoning profile above is a different experiment.

The previous official Qwen2.5 3B and 7B downloads remain available through `local_ai.py --size 3b` or `--size 7b`. To start an existing older GGUF with the PowerShell launcher, pass its `-Model` path and `-Preset default`.

## Earlier Qwen3.5-35B-A3B candidate

The optional candidate is [Qwen3.5-35B-A3B](https://huggingface.co/Qwen/Qwen3.5-35B-A3B), a hybrid model with 35 billion total parameters and 3 billion active parameters. It uses the [ggml-org GGUF source](https://huggingface.co/ggml-org/Qwen3.5-35B-A3B-GGUF) and the same verified native runtime. The setup converts the publisher's Q8_0 GGUF to Q4_K_M locally on the CPU. This is requantization, which can lose quality compared with quantizing original BF16 weights; the result is not a Qwen-published Q4 release. A larger model does not establish ad-detection accuracy.

Allow at least 60 GB of free disk for the source and converted model. The setup downloads 36,903,139,584 source bytes from revision `3127ef0b7fd4f12626506419ceefde54fa8db53c`, verifies source SHA256 `8a83fbfde74b0366feb76b075c10df46bbece6aa21ed14a6f291a7904f2b7d67`, and requires the b11146 Windows quantizer with SHA256 `dee025c8bd2b3e4679b766aa8fdac9fa393edd8259840f58af8832bf20708cf7`.

```powershell
.venv\Scripts\python.exe scripts\setup_qwen35_local.py
.venv\Scripts\python.exe scripts\setup_qwen35_local.py --verify-only
```

The output, source, and exact conversion command/hash manifest are saved in `.local/models/qwen3.5-35b-a3b`. The tested output is 21,166,758,144 bytes with SHA256 `e2d0b8f0283a971b90a7e1421d5e002e0e1549f25812e5fc5aa4a40a83dd0ed5`. CPU conversion took 253 seconds on the tested machine. The setup does not load a GPU model. Stop other model servers before starting this separate candidate:

```powershell
.\scripts\start_qwen35_local.ps1 -NoThinking
```

It uses the same loopback endpoint and model alias as the Qwen3 launcher. Its default is 8,192 context tokens, one inference slot, batch size 512, and microbatch size 128. The first four layers' sparse expert tensors stay on CPU; the remaining tensors eligible for GPU offload use CUDA. This measured profile uses 17,911 MiB for CUDA model weights, 2,362 MiB for CPU-mapped weights, and about 452 MiB for CUDA context/compute buffers. The machine had 2,191 MiB physically free after a small structured-output smoke request, including roughly 3,808 MiB of pre-existing desktop GPU usage.

Automatic fitting remains enabled as an additional check, but CUDA reported more available VRAM than `nvidia-smi` on this Windows system. Relying on automatic fit alone left insufficient working headroom. `-GpuLayers` and `-CpuMoeLayers` allow explicit adjustments for another machine. Use `-LogVerbosity 4` to record detailed startup allocation, and preserve the resolved configuration when freezing an evaluation candidate. The small smoke request verified typed JSON and nonthinking behavior; it does not measure ad-detection accuracy or long-input capacity.

Qwen3.5 supports `chat_template_kwargs.enable_thinking=false`. The candidate launcher follows the model card's general-task sampling settings: temperature 1.0/top-p 0.95 with thinking, or 0.7/0.8 with `-NoThinking`, plus top-k 20, min-p 0, presence penalty 1.5, and repetition penalty 1.0. Explicit client settings override server defaults. Its bounded reasoning/output limits are the same as the Qwen3 launcher; they are intentionally much smaller than the publisher's limits for long reasoning benchmarks.

## Start Castwell

Keep the Qwen server running, then use another terminal:

```powershell
.\scripts\start_castwell.ps1
```

Open `http://127.0.0.1:8000`. The launcher uses the project's `.venv`, the local speech cache, the Qwen endpoint, and the `verified` AI policy. New libraries default to review only. Existing saved review preferences are preserved. The launcher does not download models or send classification requests during startup.

`-Background` starts hidden and writes logs/PID information under `.local/logs`. `-Port`, `-DataDirectory`, `-ModelCache`, and `-SpeechModel` customize the app. Foreground servers stop with Ctrl+C. On Windows, background Python can create a child process; the PID listening on the selected port is the actual server PID.

`GET /api/settings` shows effective settings. `GET /api/diagnostics` checks installed tools, cache files, and configuration without contacting a model. A successful readiness result does not measure detection accuracy. The Settings page's **Test classifier** action makes a local test request.

## Kev as a separate comparison

[Kev](https://github.com/jaredpalmer/kev) is an open-source decision model with a TypeSafe-compatible `/v1/systemone` API. It is a separate project from the hosted Jev service. This setup uses Kev4B's adapter and pointer head on Qwen3.5-4B-Base, with the publisher's shipped calibration temperature 2.406050072164233.

Stop Qwen before loading Kev. Create its isolated Windows CUDA environment and download the pinned public weights:

```powershell
py -3.12 scripts\setup_kev_local.py
.\scripts\start_kev_local.ps1
```

The default 4B setup pins:

| Component | Revision/version |
| --- | --- |
| Kev source | `5e42a7a03f28134853dd3ff77461457e921e5ec1` |
| `jaredpalmer/kev-4b` | `6cfce5c2fa4b4bd64026336ab649c5ca78857d52` |
| `Qwen/Qwen3.5-4B-Base` | `1001bb4d826a52d1f399e183466143f4da7b741b` |
| PyTorch | `2.8.0+cu128`, official CUDA 12.8 wheel |
| Transformers | `5.19.0` |

Exact Windows package versions are in [`scripts/kev_requirements.txt`](../scripts/kev_requirements.txt). The helper verifies SHA256 hashes for every pinned loader file, including weights, the adapter and pointer head, tokenizer files, configuration and chat template. The default 4B setup records paths and provenance in `.local/kev-model-provenance.json`. It preserves an existing source checkout if its revision or local changes differ from the pinned version. Allow roughly 20 GB for its environment and weights, plus installer cache space.

The Kev launcher uses only cached weights in offline mode. It binds to `http://127.0.0.1:8083/v1/systemone`, model `kev-latest`, with BF16 Torch CUDA and SDPA. Optional fused Triton kernels and CUDA graphs are disabled for the tested Windows fallback. Prefix caching is limited to one state and 8,192 cached tokens. No Linux VM or cloud deployment is involved.

Kev emits decisions rather than generated text. For a Noul question, `noul` is the probability of true. For Choice questions, use the named entry in `probabilities`. Its `confidence` field is the normalized margin above a uniform distribution, `(p_max - 1/K) / (1 - 1/K)`, and is not the selected category's probability. General-purpose calibration does not establish accuracy or calibration on podcast ads.

Recheck all pinned loader files offline, without installing packages, downloading files or rewriting existing provenance:

```powershell
.local\kev-venv\Scripts\python.exe scripts\setup_kev_local.py --verify-only
```

For the optional 9B comparison model, stop any running Qwen or Kev server before launching it:

```powershell
py -3.12 scripts\setup_kev_local.py --size 9b
.local\kev-venv\Scripts\python.exe scripts\setup_kev_local.py --verify-only --size 9b
.\scripts\start_kev_local.ps1 -Size 9b
```

Both sizes share the configured Hugging Face cache root, by default `.local/models/huggingface/hub`, with distinct pinned snapshots. Preparing 9B preserves the cached 4B model and uses its own `.local/kev9-model-provenance.json`; offline verification does not rewrite either manifest. The isolated environment is shared. The [completed comparison](local-model-round2-2026-10-08.md) does not recommend 9B over the retained Qwen workflow. Kev is available through the comparison CLI, not the main application's classifier settings.

## Compare without changing audio

The comparison harness is separate from the app's processing queue and only writes reports. With Qwen running:

```powershell
.venv\Scripts\python.exe scripts\compare_detectors.py --backend verified-ai --ai-reasoning --split dev --model-label 'Qwen3.5-27B Unsloth Q4_K_M; llama.cpp b11146; all GPU layers; intent-boundary-v8; reasoning1024' --output .local\evaluations\qwen35-dense-development.json
```

With the default Kev 4B server running, use:

```powershell
.venv\Scripts\python.exe scripts\compare_detectors.py --backend kev --split dev --kev-model-label 'Kev4B 6cfce5c2; Torch2.8 cu128 BF16 unfused' --output .local\evaluations\kev-development.json
```

For 9B, change `--kev-model-label` to `Kev9B db029f08; Torch2.8 cu128 BF16 unfused` and choose a separate output path.

Use development cases to adjust prompts and thresholds. Freeze the full configuration before evaluating a fresh held-out set. Recording evaluations additionally need the exact `--model-file` and `--candidate-config` so their artifact and request-profile identities are checked. These transcript fixtures do not replace end-to-end testing of speech recognition, timing boundaries, and listening quality. See [the evaluation notes](ad-read-evaluation.md) for commands, results and limitations.
