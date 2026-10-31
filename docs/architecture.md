# Architecture

Castwell is a single-user FastAPI application with a SQLite library and a plain JavaScript interface. One process owns one data directory and a serial episode queue. There is no separate database service, frontend build, scheduler, or hosted inference dependency.

## Boundaries

| Component | Responsibility |
| --- | --- |
| `castwell/app.py` | HTTP validation, local-origin mutation checks, media streaming, subscription and upload endpoints, settings/model preparation, exports, and static assets |
| `castwell/middleware.py` | Exact trusted-host validation and streamed request-size limits, including uploads without Content-Length |
| `castwell/library.py` | Additive schema migrations, short SQLite transactions, metadata/state, subscriptions, bounded cut history, preferences, and snapshot backups |
| `castwell/feeds.py` | Normalize RSS/Atom metadata, enforce download limits, download media atomically, and keep credential-bearing URLs out of errors |
| `castwell/jobs.py` | Serialize work, deduplicate submissions, persist retry intent, checkpoint completed stages, and expose cancellation/progress |
| `castwell/processing.py` | Probe audio, cache speech models, validate transcripts/cuts, dispatch detection, generate waveforms, trim audio, and remap transcript timelines |
| `castwell/ad_review.py` | Intent/boundary policy, bounded contextual requests, evidence validation, and reviewable cut proposals |
| `castwell/fast_review.py`, `castwell/episode_boundaries.py` | Experimental whole-episode intent decisions, complete-scene boundary requests, exact source-word ownership, and mandatory review |
| `castwell/batched_transcription.py` | Optional CUDA speech batches with explicit batch size and timestamp restoration |
| `castwell/transcript_quality.py` | Diagnose transcript gaps and recover provisional speech with bounded local retries |
| `castwell/config.py` | Validate saved preferences, apply environment overrides, report local readiness, prepare speech models, and explicitly test classifiers |
| `castwell/static/` | Gallery, subscriptions/settings dialogs, player, transcript search, waveform, and cut editor |
| `castwell/evaluation.py`, `castwell/recording_evaluation.py` | Separate shadow comparisons on synthetic fixtures or privately held recording annotations |
| `castwell/jev.py` | Typed comparison adapters: loopback-only Kev and separately guarded, explicit paid Jev opt-in; both return unapproved cuts |
| `scripts/*local*` | Optional local model setup/launch helpers; see [local models](local-models.md) for pinned artifacts and runtime settings |

## Processing and recovery

A job moves through queued, downloading, transcribing, detecting, and rendering stages as needed. Download-only work stops after probing and waveform generation, preserving an already prepared episode's state. Downloaded audio and valid transcripts are reused. Measured duration replaces the RSS estimate before transcript validation. An HTTP-success response containing invalid media is discarded from the managed download cache so retry can fetch it again; a missing decoder does not discard audio.

The library persists an unfinished job's operation and options in `pending_job`. An ordinary retry resumes that request, including explicit redetection, retranscription, or export, across cancellation and restart. Completed transcription and detection clear their redo flags when saving results. Successful completion clears the request; a new explicit operation replaces it, and manual decision changes clear obsolete retry intent.

Queued processing also snapshots the selected speech model, language, batch size, and transcription profile. Fast detection snapshots both the episode and boundary inference profiles. An incompatible profile change prevents a silent retry under different settings. Existing speech settings default to batch size 1; the optional batch setting accepts 1 through 16 and does not replace an explicit model choice.

Existing cuts or `analysis_done` preserve reviewed decisions on ordinary processing, including an intentionally empty list. Explicit redetection replaces them only after analysis succeeds and invalidates the previous export. Retranscription uses the current speech settings, backs up the prior transcript, and preserves reviewed cuts on the same audio. Importing a transcript resets analysis. Replacing a missing original recording invalidates old timestamps because a fresh download may contain different ads. Cut history is bounded to 30 revisions per episode.

Cancellation is cooperative: processing checks a signal and stops owned subprocesses. An in-flight model load or network call may have to return first. Completed stages remain available, and duplicate active submissions are ignored. Startup marks interrupted jobs for retry. Run one application worker per library; this is not a distributed queue.

Downloads publish only complete files. Rendering writes a temporary output and atomically publishes it after success, leaving the original untouched. A failed or cancelled render cannot publish a partial replacement. Changing decisions clears the database's cleaned-output reference, so a stale file is not served as the current edit.

Transcription reports timestamp gaps of at least three seconds as unknown audio: they can contain silence, music, or missed speech. By default, the loaded speech model retries inter-segment gaps with VAD disabled, bounded to eight crops and 120 seconds of cropped audio. Only plausible, positive-duration words fully inside the original gap are inserted. Existing segment speech is preserved; internal word gaps remain diagnostic. Recovered segments carry `asr_recovered` and `requires_review`, with candidate/attempt records in `transcript.quality.recovery`. Failed recovery remains visible. Older or imported transcripts receive missing coverage diagnostics in the detail response without rewriting stored speech or decisions.

## Timeline invariants

**The original recording is the source of truth.**

1. Stored transcripts, ad intervals, waveform coordinates, revisions, and playback position use seconds on the original timeline.
2. Validated transcript segments are ordered, nonoverlapping, and within the transcript duration, which processing reconciles with measured audio. Optional word timestamps stay within their parent segment.
3. Cuts have finite bounds with `0 <= start < end <= duration`. Only approved cuts affect exports. Approved overlapping intervals merge before rendering.
4. Exporting computes retained intervals. Removing the entire recording is rejected.
5. Cleaned playback time is original time minus the approved removed intervals before that point. The UI maps the saved original position when switching versions; a position within a removed interval maps to its retained boundary.
6. Cleaned transcript exports use the same retained intervals. JSON includes a timeline mapping. Retained word timestamps are remapped; when a boundary cuts a word, its midpoint determines retention and timestamps are clipped. A segment without word alignment may retain incomplete text and carries a warning.
7. Cut edits and restores invalidate cleaned audio and removed-duration metadata. A fresh render establishes the new cleaned artifact. The MP3 container duration may include codec padding; logical transcript duration is the sum of retained intervals.

Word-aligned display segments can supply sentence units bounded by their existing word timestamps. Without word alignment, the verified policy can judge individual sentences but aggregates them back onto the original parent interval. It does not invent times for those sentences. Mixed parent content remains a proposal requiring boundary review. Classification IDs never replace stored transcript IDs; the provider supplies labels for known IDs, and local code supplies timestamps. The verified policy merges only adjacent proposals with touching times and compatible review states, so editorial units and positive gaps separate cuts.

## Detection policy

New libraries default to `review_only=True`, `ai_policy="verified"`, and a 0.90 approval threshold. Saved preferences persist. `auto` uses contextual AI when both endpoint and model are configured, otherwise local rules. Partial configuration and provider failures are errors. With the verified policy, rule-only suggestions always require review.

The current verified policy is `intent-boundary-v8`. Each target receives separate intent and boundary judgments from the same model, using surrounding transcript text. Both passes must cover every target exactly once with a recognized label, finite confidence, reason, and evidence source ID from the supplied context. The application attaches that unit's exact original text to the audit record; model-generated evidence text is rejected. Audio and private feed URLs are not sent. Windows are bounded to 24 targets and 4,500 target characters, with bounded neighboring context. Invalid or incomplete responses may trigger smaller target windows up to five subdivision levels, reaching individual targets; unresolved failures save no new analysis and do not switch detectors. Transport failures and cancellation stop the operation without subdivision.

The v8 policy distinguishes editorial source credits, citations and mentioned URLs from commercial offers or sponsorship disclosures. When both passes label a unit editorial, it is omitted regardless of their confidence scores. Explicit uncertainty, mixed labels and disagreement remain review suggestions. The commercial approval threshold and alignment safeguards are unchanged. Correct source-credit classification remains a model judgment, not a deterministic guarantee. The optional saved `ai_reasoning` setting selects a bounded Qwen3.5 reasoning request profile; its default is false, with no environment override. Enabled requests explicitly set thinking, a 1,024-token reasoning budget, a 4,096-token output cap and fixed sampling parameters. The application sends its chosen profile even when the server's default differs. Evaluation reports record that resolved profile; cuts record its name. Changing the model or profile requires a distinct candidate record.

A unit passing both checks as commercial can be eligible for automatic approval only if confidence meets the threshold, alignment is acceptable, and review-only is disabled. Recovered speech and words with low alignment probability block automatic approval. Mixed content, explicit uncertainty, disagreements, and low-confidence commercial judgments remain review proposals. Two editorial judgments produce no proposal regardless of their confidence scores. The passes are correlated requests to one model; their agreement and reported confidence do not establish accuracy.

The explicit `legacy` policy retains the earlier single-pass `ads` protocol and may preserve unmatched rule cues as unapproved suggestions. Recovered-speech overlap also prevents automatic approval in legacy AI and heuristic paths. Direct `detect_ads` calls without application settings retain legacy defaults for compatibility and comparison; application jobs receive the saved settings.

### Experimental whole-episode policy

The explicit `fast` policy reads the episode before refining its commercial boundaries. Its removal scope is sponsor/product reads. Unpaid parody, editorial discussion, program announcements, and promotions for other free podcasts remain protected. The classifier decides intent; local code assigns timestamps from the original aligned words.

The tested local profile requires a loopback llama.cpp service with native token counting. It preflights complete requests against the server's real context capacity and the output budget. A recording that fits is read in one episode request. Longer recordings use complete-source overlapping windows, with exact coverage and conflicting labels retained as uncertainty. The boundary pass receives whole identified scenes and neighboring protected context. It classifies only scene-edge words and mixed or uncertain units, leaving globally protected units unchanged.

The current whole-episode request uses a 6,144-token reasoning budget and an 8,192-token total output cap. Boundary requests use a separate 1,024-token reasoning budget and a 2,048-token total output cap. These fixed profiles apply independently of the general Qwen reasoning checkbox. The default timeout is 300 seconds per classifier request, with the existing explicit `CASTWELL_AI_TIMEOUT` override supported. A timeout is a failure limit, not a promise that an episode finishes within that time.

This policy is experimental and always requires an explicit review action, including when it proposes no cuts. It does not turn an empty result into automatic approval. In version 0.5.3, the reviewed-Qwen desktop profile defaults to fast when there is no explicit scene/fast selection, and uses batch size 8 only when no batch preference exists. Saved desktop policy takes precedence over saved source policy; explicit choices and hosted-provider configuration remain stored unchanged. Older implicit scene defaults can migrate to fast with mandatory review. The exact reviewed artifact receives a 32,768-token native context; other local models retain their existing 8,192-token context and defaults.

Successful fast detection writes an atomic `fast-analysis.json` sidecar inside the episode's private directory. It retains global labels, source coverage, boundary request identities, all word decisions, and unresolved word IDs, tied to transcript and proposed-cut fingerprints. It contains no API key. The job validates this evidence before replacing saved decisions. Its flags explicitly state that manual review is required and automatic acceptance is unvalidated.

A separate deterministic omission guard checks globally editorial units for explicit sponsorship, offer codes, or transactional calls to action with offer terms. A flag stops successful analysis before boundary inference and preserves prior decisions. The guard never changes a label or creates a cut; an unpaid parody can conservatively trigger it. Successful audits include the negative guard result, which Jobs recomputes from the canonical source and global decisions. Absence of these cues cannot establish that all advertisements were found.

Proposals and approval are separate states:

| Field or view | Meaning |
| --- | --- |
| Candidate/proposed cuts | All suggested intervals, including mixed or uncertain material; presence alone authorizes no deletion |
| `requires_review` | The detector or later adjustment requires review; the flag can remain true after a user explicitly selects the cut |
| `approved` | The selection consumed by rendering; overlapping approved intervals are unioned |
| `verification`, `policy_version` | Model judgments and policy provenance for the detected bounds, not a correctness guarantee |

When the frontend changes a detected cut's boundary, it retains `detected_bounds` and `manual_adjustment` metadata, marks review required, and clears approval. Existing evidence and confidence describe the original detected bounds. The user can listen and explicitly select the adjusted interval. Saving preserves this metadata; exporting uses the resulting approved bounds. Recovered transcript lines are marked, and gap preview controls play the original audio timeline.

## Storage and privacy

SQLite uses WAL mode and short transactions. Schema changes add fields/tables without discarding existing episodes. A one-time migration creates subscriptions for legacy RSS entries. Subscription removal preserves episodes and is not reversed during future startups. Settings store non-secret values only; API keys come from the environment.

Feed URLs, media URLs, and local audio paths stay out of public episode responses. Public subscription listings expose a hostname instead of the original URL, redact unsafe artwork, and use generic refresh errors. OPML export omits URLs with user information or queries and reports its omission count in a response header. Tokens embedded in URL paths cannot be identified reliably, so an OPML export still needs private handling.

The data directory contains `library.sqlite3`, original media, and cleaned exports. SQLite includes transcripts, cuts/history, flags, subscriptions, and settings. Models can live in a separate cache. Complete backups must coordinate both database and media; stop the server before copying the directory. `Library.backup(path)` uses SQLite's backup API to include committed WAL changes and atomically publish a standalone database snapshot, but does not copy audio. Stored media paths are absolute, so a full restore should retain the original data-directory path.

The application has no user accounts. It binds to loopback by default, validates request hosts to resist DNS rebinding, rejects browser mutations from other origins, and serves a restrictive content security policy. Loopback hosts are allowed by default; `CASTWELL_ALLOWED_HOSTS` adds exact trusted hostnames/IPs for deployments, without wildcard matching. Host validation covers reads as well as mutations. These protections do not turn it into a public multi-user service. Place any remote access behind an appropriate private or authenticated boundary.

## Verification boundaries

Tests use local HTTP/RSS and audio fixtures, controlled model output, and classifier responses. FFmpeg checks exercise decoding, trimming, export durations, waveforms, chapter removal, and original-file preservation. Other checks cover complete evidenced model responses, provisional speech, durable retries, manual-boundary provenance, migrations, privacy, revisions, settings, and SQLite backups.

CI defines Python 3.10/3.12 unit jobs, JavaScript syntax checks, and a Chromium smoke test with isolated fixtures. These checks use no model downloads or paid APIs. Model preparation and classifier connection tests establish readiness, not ad accuracy. Separate [evaluation workflows](ad-read-evaluation.md) distinguish proposals, verification eligibility, and approved cuts, record failures, and preserve input/code/model provenance. Real-recording references and transcripts remain private; public summaries contain aggregate results and limitations. Representative recordings and listening around boundaries remain necessary to assess practical detection quality.

### Five-minute episode benchmark

`scripts/benchmark_episode.py` measures one locally available episode against a frozen, previously consumed reference. The goal is no more than 300 seconds of total elapsed processing per 3,600 seconds of source audio. The stopwatch begins before adapter construction and model preparation, then covers transcription, detection, and rendering. Normalized time is derived from this single wall clock, not a sum of favorable timings from separate runs. RSS transfer time and one-time model downloads are outside this local-file benchmark and must be reported separately. Input validation, provenance hashing, and duration probing happen before the processing stopwatch and their elapsed time is reported separately. Operating-system disk caches are not flushed.

Run it with a trusted local lifecycle adapter and a fresh private output directory. The following paths are examples for artifacts prepared by the evaluator, not files shipped with Castwell:

```powershell
python scripts/benchmark_episode.py `
  --audio .local/evaluation/episode.mp3 `
  --reference .local/evaluation/consumed-policy-overlay.json `
  --configuration .local/evaluation/frozen-configuration.json `
  --adapter-file .local/evaluation/cuda-adapter.py `
  --model-file .local/models/classifier.gguf `
  --model-file .local/models/whisper/model.bin `
  --source-file castwell/fast_review.py `
  --source-file castwell/processing.py `
  --output-dir .local/evaluation/full-cold-run `
  --candidate-preview
```

The adapter module defines `build_adapter(context)` without starting models at import time. The context supplies configuration, output directory, and request telemetry. The returned object implements `prepare()`, `transcribe(audio)`, `detect(transcript)`, `render(audio, output, cuts)`, `evidence()`, and `close()`. An optional `prepare_episode(audio)` phase includes waveform creation and isolated application/database preparation. If this hook is absent, the report explicitly describes the run as pipeline timing without application queue/database overhead. Include additional model and source files with repeated flags to identify every candidate input.

Record every inference request through `context["telemetry"].record_request(...)`, including elapsed time, success, stage, known input/output tokens, and whether the request used cached predictions or a paid provider. Missing usage stays unknown. Runtime evidence must establish CUDA execution, cold model startup, zero transcript/prediction cache hits, no asset downloads, and complete request accounting before the run can pass the cold CUDA timing target. A cached transcript supplied with `--transcript` is a diagnostic only and cannot pass that target.

`--candidate-preview` renders proposals into this separate experiment directory without approving an episode in the application. Accuracy reports count missed commercial word duration and selected protected word duration on the original audio timeline; partial overlaps and zero-duration words are reported separately. Source recordings, frozen reference transcripts, candidate sources, model files, and configuration are fingerprinted. These references have already influenced development, so a good score remains consumed development evidence. It cannot establish production accuracy, unseen-episode generalization, or acoustic listening acceptance.
