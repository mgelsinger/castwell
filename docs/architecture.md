# Architecture

Castwell is a single-user FastAPI application with a SQLite library and a plain JavaScript interface. One process owns one data directory and a serial episode queue. There is no separate database service, frontend build, scheduler, or hosted inference dependency.

## Boundaries

| Component | Responsibility |
| --- | --- |
| `castwell/app.py` | HTTP validation, local-origin mutation checks, media streaming, subscription and upload endpoints, settings/model preparation, exports, and static assets |
| `castwell/middleware.py` | Exact trusted-host validation and streamed request-size limits, including uploads without Content-Length |
| `castwell/library.py` | Additive schema migrations, short SQLite transactions, metadata/state, subscriptions, bounded cut history, preferences, and snapshot backups |
| `castwell/feeds.py` | Normalize RSS/Atom metadata, enforce download limits, download media atomically, and keep credential-bearing URLs out of errors |
| `castwell/jobs.py` | Serialize work, deduplicate submissions, cooperative cancellation, retain completed stages, and expose progress |
| `castwell/processing.py` | Probe audio, cache speech models, validate transcripts/cuts, classify commercial spans, generate waveforms, trim audio, and remap transcript timelines |
| `castwell/config.py` | Validate saved preferences, apply environment overrides, report local readiness, prepare speech models, and explicitly test classifiers |
| `castwell/static/` | Gallery, subscriptions/settings dialogs, player, transcript search, waveform, and cut editor |
| `scripts/local_ai.py` | Optional loopback-only llama.cpp server with a pinned, SHA256-verified official Qwen download |

## Processing and recovery

A job moves through queued, downloading, transcribing, detecting, and rendering stages as needed. Download-only work stops after probing and waveform generation. Already downloaded files and valid transcripts are reused. Measured audio duration replaces the RSS estimate before transcript validation.

An existing cut list or `analysis_done` state prevents an ordinary retry from silently replacing reviewed decisions. This includes an intentionally empty decision list. Explicit redetection replaces analysis, records the previous cuts when they change, and invalidates any old export. Importing a transcript resets analysis. Before a manual edit, restore, or relevant analysis replacement, previous decisions are saved in a history bounded to 30 entries per episode.

Cancellation is cooperative: long processing steps check a cancellation signal and subprocesses are stopped when possible. An in-flight model load or network call may have to return before it can observe cancellation. Completed downloads, transcripts, and review state survive. Cancelled or failed jobs can be submitted again; duplicate active submissions are ignored. Startup marks jobs interrupted by a prior process exit for retry instead of pretending they completed. The queue is not a distributed task system: do not run multiple application workers against one library.

Downloads publish only complete files. Rendering writes a temporary output and atomically publishes it after success, leaving the original untouched. A failed or cancelled render cannot publish a partial replacement. Changing decisions clears the database's cleaned-output reference, so a stale file is not served as the current edit.

## Timeline invariants

**The original recording is the source of truth.**

1. Stored transcripts, ad intervals, waveform coordinates, revisions, and playback position use seconds on the original timeline.
2. Every transcript segment is ordered, nonoverlapping, and within the measured audio duration. Optional word timestamps stay within their parent segment.
3. Cuts have finite bounds with `0 <= start < end <= duration`. Only approved cuts affect exports. Approved overlapping intervals merge before rendering.
4. Exporting computes retained intervals. Removing the entire recording is rejected.
5. Cleaned playback time is original time minus the approved removed intervals before that point. The UI maps the saved original position when switching versions; a position within a removed interval maps to its retained boundary.
6. Cleaned transcript exports use the same retained intervals. JSON includes a timeline mapping. Retained word timestamps are remapped; when a boundary cuts a word, its midpoint determines retention and timestamps are clipped. A segment without word alignment may retain incomplete text and carries a warning.
7. Cut edits and restores invalidate cleaned audio and removed-duration metadata. A fresh render establishes the new cleaned artifact. The MP3 container duration may include codec padding; logical transcript duration is the sum of retained intervals.

Classification may split word-aligned display segments into sentence units. Those internal IDs are used only in classification requests; they never replace IDs in the stored transcript. The provider returns IDs rather than arbitrary timestamps, and every response is validated against the window that supplied those IDs. Overlapping context supports transitions, while each core window owns its own segment decisions.

## Detection policy

`auto` uses contextual AI when the endpoint and model are both configured, otherwise local rules. Partial AI configuration and provider failures are explicit errors. The AI receives transcript text and IDs, not audio or private feed URLs. An AI response must contain a valid `ads` list, consecutive recognized segment IDs, finite confidence, and a reason. Results are not persisted until the full analysis validates.

Commercial cues missed by AI can remain as unapproved review suggestions; a heuristic guess cannot silently override an AI rejection into an automatic cut. Automatic approval obeys the saved confidence threshold and review-only preference. Confidence is an estimate supplied by the detector, not evidence of calibrated accuracy.

## Storage and privacy

SQLite uses WAL mode and short transactions. Schema changes add fields/tables without discarding existing episodes. A one-time migration creates subscriptions for legacy RSS entries. Subscription removal preserves episodes and is not reversed during future startups. Settings store non-secret values only; API keys come from the environment.

Feed URLs, media URLs, and local audio paths stay out of public episode responses. Public subscription listings expose a hostname instead of the original URL, redact unsafe artwork, and use generic refresh errors. OPML export omits URLs with user information or queries and reports its omission count in a response header. Tokens embedded in URL paths cannot be identified reliably, so an OPML export still needs private handling.

The data directory contains `library.sqlite3`, original media, and cleaned exports. SQLite includes transcripts, cuts/history, flags, subscriptions, and settings. Models can live in a separate cache. Complete backups must coordinate both database and media; stop the server before copying the directory. `Library.backup(path)` uses SQLite's backup API to include committed WAL changes and atomically publish a standalone database snapshot, but does not copy audio. Stored media paths are absolute, so a full restore should retain the original data-directory path.

The application has no user accounts. It binds to loopback by default, validates request hosts to resist DNS rebinding, rejects browser mutations from other origins, and serves a restrictive content security policy. Loopback hosts are allowed by default; `CASTWELL_ALLOWED_HOSTS` adds exact trusted hostnames/IPs for deployments, without wildcard matching. Host validation covers reads as well as mutations. These protections do not turn it into a public multi-user service. Place any remote access behind an appropriate private or authenticated boundary.

## Verification boundaries

Tests use local HTTP/RSS and audio fixtures, controlled model output, and classifier responses. Real FFmpeg exercises decoding, timeline trimming, exported durations, waveform generation, metadata removal, and original-file preservation. Library tests cover additive migration, state preservation, privacy, bounded revisions, settings, and a snapshot with committed WAL pages.

CI runs these tests on Python 3.10 and 3.12, checks JavaScript syntax, and exercises the gallery through Chromium with isolated local fixtures. The browser smoke script checks uploads, review, real audio exports/playback, restored revisions, downloaded artifacts, subscription management, and desktop/mobile layouts. CI does not download model weights or contact paid services. Model preparation confirms that a selected speech model loads; the classifier connection test validates response format on fixed synthetic text. The optional `scripts/evaluate_classifier.py` exercises six included semantic examples against a live classifier and records selections. None of these measures advertisement-removal accuracy on a listener's collection. Representative podcasts, different ad styles, and manual listening around edit boundaries remain necessary to evaluate that behavior.
