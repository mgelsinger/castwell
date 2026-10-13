# Castwell

**CUDA podcast preparation, batch ad removal, and cuts you control.**

Prepare batches of podcast episodes on NVIDIA CUDA, identify real promotional reads, preserve unpaid jokes and program speech, and export copies with your approved cuts. Choose **Remove ads** or **Keep original** for each podcast. Originals stay available, and every cut can be reviewed or restored. Reliable unattended ad removal remains the release goal; the current candidate requires review.

**Removal scope:** sponsor/product reads only. Preserve promos for other free podcasts. Real sponsor, product, paid-service and paid-subscription reads remain targets even when humorous.

[Get started](#start-listening) | [Watch the tour](#see-it-in-action) | [Production operation](docs/production.md) | [Docker](#docker) | [Local AI](#run-the-classifier-locally) | [Ad-read evaluation](docs/ad-read-evaluation.md) | [Configuration](#configuration-and-backups) | [Development](#development)

**Five-minute target:** the 0.5.3 source candidate processed a complete **75:13 episode in 5:15**, equivalent to **4:11 per hour of audio**, on an RTX 3090 Ti. This included cold CUDA model loading, fresh transcription, whole-episode detection, boundary review, waveform/database preparation, and export. Initial downloads were excluded. It uses batched large-v3 and native llama.cpp with Qwen, without CPU neural inference. See the [timing, accuracy, and model tradeoffs](docs/evaluations/2026-10-13-five-minute-cuda.md).

**Accuracy still requires review.** The final run preserved every labeled program word and the reference-labeled rutabaga parody, but left **1.31 seconds of commercial word duration** outside the cuts across 14 word entries. These are consumed development recordings with provisional speech timings, not independent listening-verified acceptance. The app rejects incomplete analyses and suspicious sponsor omissions, and fast mode always requires saved manual review. Speed acceptance is not production accuracy acceptance.

The published 0.5.1 downloads contain the older detector, which took [106:34 to classify a 79:55 episode](docs/evaluations/2026-10-11-full-stdwytk-cuda.html). Frozen 0.5.2 candidates predate the current scope and fast path. Their failed accuracy results and successful distribution checks remain in the [historical evaluation](docs/evaluations/2026-10-12-ad-removal-system.md). See the [acceptance plan](docs/ad-removal-production-plan.md) for the remaining release requirements.

![Castwell suite with a featured episode, podcast shelves, and continue listening in a synthetic demo library](docs/images/suite-desktop.png)

## Prepare episodes, review cuts, and export batches

- **Choose ads per podcast.** Save **Keep original** or **Remove ads** for each show, or update several shows together. Preparation follows those choices within a mixed batch. Keeping ads skips ad detection; removing ads prepares suggestions for approval.
- **Manage whole batches.** Select matching episodes or a podcast to download, prepare, retry, cancel, render approved cuts, or update favorites, played status, and archives. Download a ZIP with preferred, original, or cleaned audio and a report for unavailable items.
- **Find the moment.** Create local Whisper transcripts, search the text, and jump straight to a timestamp. Keep listening with the mini player while you browse.
- **Review every cut.** Inspect suggested advertisements on the waveform, adjust their boundaries, and export the selections you approve. Originals and recent cut revisions stay available.
- **Bring your episodes.** Subscribe by RSS, import OPML, or upload recordings. Filter shows and the review queue, save favorites, and resume playback.
- **Keep your options open.** Use local rules, run an optional local AI classifier, or connect an OpenAI-compatible service. Download audio and transcripts, or use Library RSS in a reachable podcast player.

## See it in action

<details>
<summary>Mobile shelves and batch controls</summary>

The same library adapts to your phone, with horizontal podcast and listening shelves:

<img src="docs/images/suite-mobile.png" alt="Castwell mobile podcast shelves and continue listening using synthetic demo episodes" width="390">

Select matching episodes to prepare downloads, apply each show's ad preference, manage your queue, and export saved approvals:

![Castwell suite batch controls with selected synthetic episodes](docs/images/suite-batches.png)

</details>

The following GIF and video demonstrate the earlier gallery layout and its listening and review tools:

[![Animated tour of the Castwell podcast library and ad review](docs/media/castwell-tour.gif)](docs/media/castwell-tour.mp4)

**[Watch or download the MP4 tour](docs/media/castwell-tour.mp4)**

<details>
<summary>Explore the ad review panel</summary>

Open an episode to review its transcript, compare original and cleaned playback, and choose exactly what to cut:

![Castwell ad review with waveform and editable cut decisions](docs/images/ad-review.png)

</details>

*The suite screenshots show the running application with a temporary synthetic library, generated tone audio, and manually seeded listening and cut states. No speech or advertisement model was used to make them. The older gallery screenshot, ad-review screenshot, GIF, and video also use synthetic demo data and depict the earlier layout. These media demonstrate the interface, not detection accuracy. See [validation results](docs/validation.md) for the checks performed and their limitations.*

## Start listening

### Windows desktop package

Download **Castwell-0.5.1-Windows-x64-Setup.exe** or **Castwell-0.5.1-Windows-x64-Portable.zip** from [GitHub Releases](https://github.com/mgelsinger/castwell/releases/latest). The installer creates a current-user Start menu shortcut. For the portable version, extract the entire archive and open `Castwell\Castwell.exe`.

The packages include Python, FFmpeg, Whisper's engine, and CUDA llama.cpp. They require a supported NVIDIA GPU and compatible driver, check CUDA and float16 support before starting, and do not fall back to CPU inference. Choose the data folder and optional local GGUF classifier in the launcher, then select **Start**. Speech and classifier weights are separate. Prepare a free speech model explicitly in Settings. The pinned Qwen3.8 model automatically selects its reviewed bounded reasoning profile; other GGUF files use a generic local profile. Keep manual cut review enabled.

Installed libraries live in `%LOCALAPPDATA%\Castwell` and survive uninstall. Portable libraries live in `Data` beside the executable. Open an existing library with `--data-dir` after stopping its previous service. These artifacts are unsigned; compare the release checksum before running them. See the [Windows guide](docs/windows.md) for model preparation, launch options, updates, and distribution verification.

### Run from source

Clone [mgelsinger/castwell](https://github.com/mgelsinger/castwell) on your device:

```bash
git clone https://github.com/mgelsinger/castwell.git
cd castwell
```

The native application requires **Python 3.10+**, a supported **NVIDIA CUDA GPU**, compatible CUDA 12 and cuDNN 9 runtime libraries, and **FFmpeg** (`ffmpeg` and `ffprobe` on your PATH). On Debian or Ubuntu, install FFmpeg with `sudo apt-get install ffmpeg`. Native macOS inference is unsupported by the current CUDA-only runtime. Alternatively, the [Docker setup](#docker) includes Python and FFmpeg.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[transcription]'
python -m pip uninstall -y onnxruntime onnxruntime-gpu
python -m pip install --no-deps --force-reinstall onnxruntime-gpu==1.23.2
python -m castwell --data-dir ./data
```

On Windows PowerShell, create the environment with `py -m venv .venv` and activate it with `.\.venv\Scripts\Activate.ps1`, then run the same install and start commands. Install FFmpeg separately and confirm `ffmpeg -version` and `ffprobe -version` work in that terminal. If compatible native CUDA libraries are not already available, run `python scripts/fetch_windows_dependencies.py --output .local/windows-vendor-cuda` to explicitly download the free pinned runtime files. Apply the ONNX GPU reinstall commands last so a CPU package does not replace its shared Python namespace. The desktop packages already bundle these native dependencies.

A fresh checkout includes the application code. Speech models, classifier weights, and your podcast library are prepared or downloaded on your device; they are not included in GitHub.

Open **http://127.0.0.1:8000**. Castwell is a single-user application with no login; its default address is accessible only from the local machine. Keep the service on a trusted machine or behind authenticated private access. `python -m castwell --help` lists the host, port, storage, and speech-model options.

On an already configured Windows installation, `./scripts/start_suite.ps1 -ReviewedProfile` starts the verified local classifier and Castwell together, checks the installed speech model and library, and writes a readiness report. Use `./scripts/stop_suite.ps1` to stop its recorded services while idle. The [production guide](docs/production.md) explains installation checks, custom paths, backups, recovery, and the full-podcast acceptance test.

The current source also supports `./scripts/start_suite.ps1 -FastProfile` for experimental whole-episode review. This explicit flag selects the pinned CUDA profile, enables manual approval, and saves speech batch size 8, overriding those two saved preferences. Saved or explicitly chosen speech models remain respected. The [fast profile instructions](docs/production.md#start-the-suite-on-windows) explain the limits; selecting it does not establish production accuracy.

For a private deployment using another hostname, set `CASTWELL_ALLOWED_HOSTS=castwell.lan,192.168.1.25` to the exact hostnames or IP addresses clients will use. Loopback hosts are always allowed; other request hosts are rejected unless listed. Wildcards are not supported. This setting works alongside the listen address or reverse proxy and does not add authentication.

1. **Add a podcast or upload audio.** Use an RSS feed, import subscriptions from OPML, or upload MP3, WAV, M4A, MP4, FLAC, OGG, Opus, or AAC recordings up to 2 GB. Identical uploads are deduplicated.
2. **Prepare the speech model.** In Settings & models, choose a Whisper model, save settings, and select **Download model**. The default source model is `base`; speech inference uses CUDA float16, and speech activity detection uses GPU ONNX Runtime with CPU fallback disabled. Environment check reports whether its files are available.
3. **Choose each podcast's ad preference and prepare episodes.** **Keep original** downloads and transcribes without detecting ads. **Remove ads** makes a waveform and transcript, then proposes ad cuts for review. Use an episode, selected batch, or whole podcast. Review is enabled by default; only selections you approve are exported. **Download originals** prepares audio without transcription or detection.
4. **Review and listen.** Open Ad review, listen around the suggested boundaries, adjust timestamps, or use **Mark start / Mark end** while listening. Select the cuts you want, then export cleaned audio.

The gallery supports podcast and status filters, favorites, archived episodes, played/unplayed state, search, and sorting. Playback resumes at your saved position; change speed, skip 15 seconds, or keep listening through the mini player after closing the episode. The waveform shows the original timeline and proposed cuts. Search transcript text and click timestamps to hear the passage.

The catalogue displays 48 matching episodes at a time; **Show more** adds another page. **Select matching episodes** includes every matching item, including pages you have not opened. Selections of 50 or more episodes ask for confirmation with the full count before downloading or preparing audio. Batch submissions are split into groups of up to 500. Audio ZIPs accept up to 500 episodes with a combined 10 GiB audio and transcript payload limit, so export large listening collections in smaller selections. Unchanged library polls reuse an in-memory response instead of transferring the catalogue again.

## Subscriptions and exports

Manage subscriptions to refresh one podcast or all podcasts and collect new episodes. Refresh all reports each subscription's progress and failures while the library stays available. Refreshing preserves downloaded audio, transcripts, listening state, podcast ad preferences, and reviewed cuts. Unsubscribing keeps existing episodes and their show preference. Refresh is manual; there is no background subscription schedule.

OPML imports accept up to 100 feeds per file and report individual failures without discarding successful imports. **Export OPML** omits subscription URLs containing user information or query parameters. The response header `X-Castwell-Private-Feeds-Omitted` gives the omitted count. Some providers place credentials in URL paths, which cannot reliably be recognized; treat exported subscription files as private too.

Episode tools provide:

- Original and cleaned audio playback and downloads.
- TXT, JSON, SRT, and VTT transcript downloads on the original or cleaned timeline.
- Editable ad decisions and the last 30 cut revisions per episode. Restoring a revision requires exporting again to apply it to audio.
- A JSON decision export containing the transcript, original timestamps, and cut decisions.
- A batch ZIP containing the requested audio versions and `report.json`, including omitted or unavailable items. Preferred exports use each show's ad preference. Audio is snapshotted on disk, with a combined 10 GiB audio and transcript payload limit and two concurrent archives; choose smaller selections for large libraries.
- **Library RSS**, at `/api/export/feed`, for prepared episodes. It follows each show's preference, using originals for keep shows and cleaned copies or completed no-cut analyses for remove shows; archived episodes are excluded. Audio links point to this Castwell server, so the player reading the feed must be able to reach it.

Changing cuts or importing a replacement transcript invalidates the previous cleaned export. Downloads and rendered audio are published atomically. Jobs run one at a time, expose progress, and can be cancelled; cancellation retains completed work. Retrying reuses downloaded audio and valid transcripts, including an explicitly reviewed empty set of cuts. A fresh detection must be requested explicitly to replace prior decisions. **Regenerate transcript** runs speech recognition again while preserving reviewed cuts unless you also request new detection. After a restart, interrupted jobs are marked for retry.

Batch results identify each queued, skipped, updated, or failed item. A show preference is captured when preparation is queued and retained for durable retries. Changing the preference updates playback and preferred exports without deleting transcripts, cut history, or an existing cleaned recording. Batch rendering follows saved approvals and skips keep-original shows.

## Advertisement detection

Two detection methods are available:

| Method | Behavior |
| --- | --- |
| Local rules | Recognizes English sponsorship language, commercial calls to action, and familiar transitions. Available without an AI service; subtle or unfamiliar ads need review. |
| Contextual AI | Checks commercial intent and edit boundaries in two separate passes. Each judgment selects a transcript passage, and the application attaches its original text. The policy distinguishes paid humor from unpaid parody and editorial quotations, but models can still confuse them. Disagreements and mixed speech require review. |

**Automatic** uses contextual AI when an endpoint and model are configured, otherwise local rules. The default verified policy reviews every transcript unit, including possible negatives. Agreed editorial judgments suppress keyword-only false positives. Local rules produce unapproved suggestions under this policy. An AI failure, incomplete configuration, or invalid response produces an error; it does not count as a successful no-ad result.

**Review every suggestion is on by default.** Listen and select the cuts yourself. If you turn review off, automatic approval still requires both AI passes to label the entire unit commercial at the adjustable threshold, initially **0.90**, with usable speech alignment. Recovered speech and conflicting judgments remain unapproved. Confidence is a model estimate, not a measured probability. The [measured results](docs/readiness-2026-10-08.md) include editorial speech passing both checks; this release does not establish safe unattended removal. Blended promotions, music-only ads, unclear speech, and timestamp errors can all produce mistakes.

Approved overlaps are merged. FFmpeg trims decoded audio and writes a separate 192 kbps MP3; a selection that would remove the entire recording is rejected. The classifier uses word-aligned sentence boundaries when available to reduce cuts that include surrounding conversation. For imported transcripts without word timestamps, a partially cut segment retains its text with a partial-text warning; its remaining words cannot be inferred precisely.

### Run the classifier locally

The reviewed local candidate uses **Qwen3.8-27B dense**, a pinned 17.1 GB Unsloth Q4_K_M file, bounded reasoning and native llama.cpp. The [matched comparison](docs/standardized-model-comparison-2026-10-08.md) found its proposals better at preserving conversation, with a small missed-ad tradeoff. Keep manual approval enabled. The classifier is separate from the Whisper speech model.

The [Qwen3.5 setup](docs/local-models.md#qwen35-27b-dense-v8-candidate) remains available as a baseline. Earlier results and their reference corrections are preserved in the [historical comparison](docs/local-model-round2-2026-10-08.md).

For Windows with an NVIDIA GPU, use the standalone **llama.cpp** server. The exercised version is [b11146](https://github.com/ggml-org/llama.cpp/releases/tag/b11146), using its Windows CUDA 12.4 x64 server and matching CUDA runtime archives. Follow the [archive verification instructions](docs/local-models.md#prepare-native-llamacpp) and extract both into `.local/llama.cpp`. This path does not require Ollama or `llama-cpp-python`. Download and verify the pinned dense model, then start the frozen server profile:

```powershell
.\.venv\Scripts\python.exe scripts/setup_qwen38_dense_local.py
.\scripts\start_qwen38_dense_local.ps1 -NoThinking -LogVerbosity 4 -Background
```

This profile loaded all 66 layers on the tested RTX 3090 Ti with 24 GB VRAM. Other machines may need different offload settings. The setup checks the publisher file's pinned hash; no local requantization is performed. Start Castwell in another terminal:

```powershell
.\scripts\start_castwell.ps1 -Background
```

In **Settings > Contextual AI connection**, enable **Use local Qwen reasoning** and keep **Require my approval for every cut** enabled. The saved `ai_reasoning` setting defaults to false. The evaluated profile explicitly enables reasoning per request with a 1,024-token budget and 4,096-token output cap, even though the frozen server was started with `-NoThinking`. Omitting that launcher switch changes the server default; it does not override the app's setting. See [the exact profile and provenance](docs/local-models.md#qwen38-27b-reviewed-profile).

Reasoning classification can take time: the matched Qwen3.8 run recorded about 16.8 minutes across 18 requests. Downloads, speech recognition and model startup are additional. This small workload does not establish full-episode processing speed.

The launchers bind to loopback and record process IDs and logs under `.local/logs`. Omit `-Background` to keep a server in the current terminal. They refuse to start if the chosen port is occupied. The `.local` directory is ignored by Git. To stop a background instance, stop its process; for the Python app on Windows, the HTTP listener can be a child of the recorded launcher PID.

The earlier **Qwen3.5-35B-A3B** experiment used a locally requantized Q4 model with four CPU expert layers in the measured 24 GB GPU setup. Its v6 results do not support unattended removal. The [historical local model setup](docs/local-models.md#earlier-qwen35-35b-a3b-candidate) preserves the exact commands and identities; the current desktop profile uses CUDA throughout inference.

The smaller **Qwen3-14B Q6_K** profile remains available with `.\.venv\Scripts\python.exe scripts/local_ai.py --download --size 14b --cache-dir .local/models --backend native --server-binary .local/llama.cpp/llama-server.exe --threads 8`, followed on later starts by `.\scripts\start_local_ai.ps1`. Its download is approximately 12.1 GB. Qwen2.5 7B and 3B variants are also available, but the recorded comparisons found consequential detection errors in the smaller candidates. Run only one model server on the shared endpoint.

Keep that terminal running. In Castwell Settings, save these values and use **Test AI connection**:

| Setting | Value |
| --- | --- |
| API base URL | `http://127.0.0.1:8081/v1` |
| Model name | `castwell-local` |
| Detection method | Contextual AI or Automatic |
| Use local Qwen reasoning | On for the supported Qwen3.5/3.8 dense profiles; off for the earlier nonthinking profiles |
| Require my approval for every cut | On |

The helper listens only on `127.0.0.1` and needs no API key. `--port`, `--threads`, and `--context` adjust the server; the default context is 8192 tokens. Use `python scripts/local_ai.py --help` for options. To use an existing Qwen-compatible GGUF file instead, run `python scripts/local_ai.py --model /path/to/model.gguf`; for a split model, point to its first shard and keep the other shard beside it. Checksum verification is automatic for the pinned `--download` models; verify the provenance of a custom file yourself.

The helper is intended for a native installation. Docker's `127.0.0.1` is the container itself; a container must use an AI endpoint reachable from its own network. The default Docker image does not compile or run the optional classifier.

For the complete pinned Windows setup, app launcher, and the separate free **Kev** typed-decision experiment, see [local model setup](docs/local-models.md). Kev is a Jev-like local model, not a local release of TypeSafe Jev. It is available in the evaluation CLI; it does not replace the main detector automatically.

### Use an existing or hosted classifier

An existing OpenAI-compatible provider must support `/chat/completions` and strict JSON-schema responses for the default verified policy. Save its API base URL and model in Settings, or set environment overrides before starting Castwell. For example, with an existing local Ollama instance and a downloaded instruction model:

```bash
export CASTWELL_AI_BASE_URL=http://127.0.0.1:11434/v1
export CASTWELL_AI_MODEL=qwen2.5:7b
python -m castwell
```

For a hosted service, use its HTTPS API base URL and model name. Supply `CASTWELL_AI_KEY` through your process environment or secret manager; credentials are never stored in the library or returned by settings. Only transcript text is sent to the classifier. Audio transcription and editing stay local. Hosted classification can incur the provider's charges.

**Test AI connection** sends a fixed synthetic transcript to check connectivity and response format. It does not send your episodes and does not measure real-podcast accuracy. Environment check itself makes no provider calls or model downloads.

## Transcripts and speech models

Transcription uses **faster-whisper**, with NVIDIA CUDA float16 inference and word timestamps. Neural speech activity detection uses the CUDA ONNX provider with CPU fallback disabled. `base` is the default multilingual model; larger models trade additional time and memory for potentially better recognition. Select a language code such as `en`, or leave it blank for automatic detection. Model preparation is explicit in Settings; the first processing run can also download missing weights.

The source transcription extra includes `onnxruntime-gpu==1.23.2`. Upstream faster-whisper also declares the CPU ONNX package, and both distributions share an import namespace. After installing the extra, remove both ONNX distributions and force-reinstall the GPU package as shown above; perform that step last after dependency changes. CUDA 12 and cuDNN 9 libraries must be available to CTranslate2 and GPU ONNX Runtime.

A local CTranslate2/faster-whisper model directory is accepted through `--model /path/to/model` or `CASTWELL_TRANSCRIPTION_MODEL`. `CASTWELL_MODEL_CACHE` controls downloaded model storage. The transcription extra constrains PyAV to `>=14,<17`; PyAV 16.1.0 has been exercised with this application.

You can import a timestamped transcript instead of transcribing. JSON must contain ordered, nonoverlapping segments within the original audio's duration. Optional word entries carry `start`, `end`, and `word`. A minimal segment-based example:

```json
{
  "language": "en",
  "duration": 25,
  "segments": [
    {"start": 0, "end": 10, "text": "Welcome to the episode."},
    {"start": 10, "end": 20, "text": "This episode is sponsored by Example. Use code PODCAST at checkout."},
    {"start": 20, "end": 25, "text": "Now back to the show."}
  ]
}
```

The downloaded recording's measured duration takes precedence over an RSS estimate. An imported transcript extending beyond the real audio fails validation. Transcript imports clear current analysis; process the episode again to detect ads. Earlier cut decisions remain in revision history when they existed.

## Docker

The NVIDIA CUDA image includes CUDA 12, cuDNN 9, FFmpeg, and GPU speech dependencies. It requires an NVIDIA GPU, compatible host driver, Docker Compose with GPU support, and NVIDIA Container Toolkit on Linux. It runs as an unprivileged user and keeps recordings and speech models in separate volumes.

```bash
docker compose up --build -d
```

Open **http://127.0.0.1:8000**. The Compose port is bound to the host's loopback interface. Prepare the speech model from Settings after the container starts. Logs and shutdown:

```bash
docker compose logs -f castwell
docker compose down
```

Named volumes persist when the container is recreated. `docker compose down --volumes` deletes those volumes; avoid it if you want to retain your library. A bind mount must be writable by container UID/GID `10001`. The supplied Compose environment can pass classifier settings and an API key from your shell. Use a reachable endpoint for a classifier outside the container; the image includes GPU speech inference and does not start a classifier server.

For a build behind a managed HTTPS proxy, Docker supports proxy build arguments and the Dockerfile accepts an optional trusted CA bundle as a BuildKit secret:

```bash
docker build --build-arg HTTP_PROXY --build-arg HTTPS_PROXY \
  --build-arg http_proxy --build-arg https_proxy \
  --secret id=build_ca,src=/path/to/trusted-ca-bundle.pem -t castwell:local .
docker compose up --no-build -d
```

The CA bundle is used only during package installation and is not stored in the image. Keep TLS and package-signature verification enabled. Container DNS and runtime proxy/trust settings must also match your network.

## Configuration and backups

Settings are saved in SQLite. Nonempty environment overrides take precedence and are identified in the settings view; remove or change an override before editing that setting in the UI.

The optional `ai_reasoning` preference is saved through Settings or `/api/settings`, defaults to `false`, and has no environment-variable override. It selects the bounded local Qwen3.5/3.8 request profile for the verified detector. Leave it off for a provider that does not support those request fields.

| Variable | Purpose |
| --- | --- |
| `CASTWELL_DATA_DIR` | Library directory; default `data` |
| `CASTWELL_ALLOWED_HOSTS` | Comma-separated additional exact trusted hostnames/IPs; loopback hosts always allowed |
| `CASTWELL_TRANSCRIPTION_MODEL` | Override the Whisper model name or local model directory |
| `CASTWELL_MODEL_CACHE` | Downloaded speech-model directory; default `<data-dir>/models` |
| `CASTWELL_AI_BASE_URL` | Override the classifier API base URL |
| `CASTWELL_AI_MODEL` | Override the classifier model name |
| `CASTWELL_AI_POLICY` | `verified` by default; `legacy` retains the earlier single-pass detector for comparison |
| `CASTWELL_AI_KEY` | Optional classifier credential, read only from the environment |
| `CASTWELL_AI_WINDOW_CHARS` | Requested central-window limit; default `18000`, capped at `4500` by verified review |
| `CASTWELL_AI_CONTEXT_SEGMENTS` | Requested neighbors on each side; default `12`, capped at `4` by verified review |
| `CASTWELL_AI_TIMEOUT` | Classifier request timeout in seconds; default `180` |

The data directory contains the SQLite library, original recordings, and derived audio. Transcripts, cut history, subscriptions, playback state, and preferences are stored in SQLite. Schema upgrades are additive. Run **one server worker per data directory**, using a local filesystem suitable for SQLite.

For a complete backup, stop Castwell and copy the entire data directory. Back up an externally configured model cache separately if you want to avoid downloading models again. Restore the directory to the **same absolute path** because stored audio paths are absolute, then start Castwell against that directory. A database-only backup does not include recordings. The storage layer also provides SQLite's snapshot backup API for callers that coordinate their own media backup.

Premium feed URLs are stored privately in SQLite and omitted from the gallery API. Keep backups and subscription exports private. No provider API key is persisted by Castwell.

Network access is needed only for sources you use: podcast RSS/audio/artwork hosts, Hugging Face model downloads (`huggingface.co`, its subdomains, and `*.hf.co`), and an optional hosted classifier. Cached local models and existing recordings can be processed without a hosted service.

## Development

```bash
python -m pip install -e '.[transcription,dev]'
python -m pytest -q
node --check castwell/static/app.js
```

The core test suite can also run with only `.[dev]` plus system FFmpeg. Tests use local feed/audio fixtures, simulated speech output, and simulated classifier responses; they do not download model weights or call paid APIs. Audio integration tests actually decode and trim recordings. The suite covers migrations, subscription privacy, uploaded audio, cancellation/retry, cut revisions, word timing, exports, settings, and API behavior. CI runs on Python 3.10 and 3.12 and checks JavaScript syntax. Model accuracy requires separate representative listening checks.

The [ad-read challenge](docs/ad-read-evaluation.md) compares detectors on authored transcripts covering genuine ads, humorous paid reads, unpaid parody, quotations, ordinary brand mentions, self-promotion, and uncertain boundaries. The original 27 cases are now development examples. A separately authored 33-case set was held out for its first comparison and has since been inspected; subsequent runs are regression checks. Results include editorial seconds wrongly selected, missed commercial seconds, boundary errors, review decisions, latency, failures, and input/code hashes. It never edits audio or changes your library:

```bash
python scripts/compare_detectors.py --backend heuristic --backend verified-ai --split dev --model-label "Exact model, quantization and runtime in use" --output development-comparison.json
```

The local model must already be running at `http://127.0.0.1:8081/v1`. Omit `--backend verified-ai` for a fully offline rule baseline. Choose settings before collecting new evaluation predictions; rerunning an inspected set does not create fresh evidence. These synthetic transcripts cannot establish accuracy on real podcasts or the benefit of vocal delivery. Free local Kev and paid TypeSafe Jev adapters are separate evaluation backends. Jev requires both `--allow-paid-api` and an explicitly named API-key environment variable; paid requests are disabled by default. Both adapters leave proposals unapproved.

The [compact standardized comparison](docs/standardized-model-comparison-2026-10-08.md) compares Qwen3.5, Qwen3.8, Kev4B, Kev9B, official paid Jev and six combinations on eight shared cases. It separates missed ads, wrongly selected editorial speech, review workload and cost. Its official Jev run cost approximately one cent at documented rates. This is a focused diagnostic, not a production accuracy guarantee.

The [follow-up policy pilot](docs/policy-pilot-2026-10-09.md) compares Qwen3.8 and official Jev on eight cases with fixed v8 and v9 instructions, including four new recording windows. The proposed revision caused a commercial-boundary regression and was withheld. Qwen3.8 with v8 and manual approval is the selected local setup; the pilot used 25 new paid requests costing approximately 2.3 cents.

The [earlier local model comparison](docs/local-model-round2-2026-10-08.md) records the retained v8 recommendation, fresh recording results and rejected alternatives. The [historical readiness report](docs/readiness-2026-10-08.md) preserves the original four-show checks and their reference limitations. The [older Qwen 2.5 7B comparison](docs/evaluations/README.md) remains available as historical evidence. Neither synthetic success nor model confidence establishes that every ad will be removed without losing editorial speech.

A repeatable browser check covers uploads, ad review, actual audio rendering/playback, restored edits, subscriptions, podcast preference persistence, mixed keep/remove batches, ZIP contents and decoded durations, bulk listening state, and desktop/mobile layouts using isolated local fixtures:

```bash
python -m playwright install chromium
python scripts/smoke_browser.py --artifacts /tmp/castwell-browser-check
```

The script uses a system Chromium if present, otherwise Playwright's browser. On a minimal Linux machine, `python -m playwright install --with-deps chromium` also installs browser system dependencies. CI runs this check on Python 3.12 and retains screenshots and the fixture server log as build artifacts. No speech or classifier model is required.

With a classifier running, an optional semantic check sends six included synthetic examples to it and records exact segment selections:

```bash
CASTWELL_AI_WINDOW_CHARS=3000 CASTWELL_AI_CONTEXT_SEGMENTS=3 \
  python scripts/evaluate_classifier.py --model-label 'Qwen2.5-7B-Instruct Q4_K_M' \
  --output /tmp/castwell-classifier-results.json
```

Its default endpoint is `http://127.0.0.1:8081/v1` and model alias is `castwell-local`; command-line flags or classifier environment variables can change them. This is a small semantic sanity check, not a representative accuracy benchmark, and it is excluded from CI.

In a live CPU run, the pinned Qwen 2.5 7B Q4_K_M model matched the expected segment selections and approvals in **five of the six** included cases. In the remaining host-read ad case, it also selected the following editorial sentence, despite reporting 0.95 confidence. This observed boundary error is why **Review every suggestion** is recommended while evaluating a classifier; a high reported confidence does not establish that a cut preserves every part of the conversation.

A real speech exercise also checked transcription, classification, manual boundary correction, and retranscription of the cleaned export. See the [recorded validation results and limitations](docs/validation.md) for that evidence and the browser/container checks.

The gallery is plain HTML/CSS/JavaScript served by FastAPI, with no frontend build step. See [architecture and timeline invariants](docs/architecture.md) before changing processing or playback.

The original download-only command remains available:

```bash
python download_podcast.py <feed_url> <output_directory>
```

It creates dated filenames, skips existing downloads, and ignores feed items without playable enclosures. Avoid putting premium subscription credentials in shared terminal history.
