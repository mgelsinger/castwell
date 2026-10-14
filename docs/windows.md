# Windows desktop installation

The Windows x64 distribution includes Castwell's desktop launcher, Python runtime, FFmpeg, the Whisper transcription engine, and a CUDA build of llama.cpp. Install Python or FFmpeg separately only if you choose to run the source checkout. Speech and classifier weights are separate downloads.

Local inference requires a supported NVIDIA GPU and an NVIDIA driver compatible with the bundled CUDA runtime. Castwell checks CUDA, float16 support, and a real GPU allocation before starting. It reports an unavailable GPU or incompatible driver and stops instead of falling back to CPU inference. FFmpeg remains available for audio preparation and rendering.

The tested configuration uses an **NVIDIA RTX 3090 Ti with 24 GB VRAM** and 128 GB system RAM. Lower-VRAM configurations have not been validated. The classifier weights and its context must fit available GPU memory; the measured results do not guarantee support on every 24 GB card.

## Choose a distribution

Version **0.5.3 is a manual-review candidate** with whole-episode CUDA detection and batched speech recognition. On the tested GPU, a fresh 75:13 episode completed in **315.16 seconds**, equivalent to **251.40 seconds per audio hour**. It selected zero protected reference word-seconds and left 1.31 commercial word-seconds outside the cuts. This meets the measured speed target; it does not establish error-free or unattended removal. See the included [five-minute CUDA evaluation](evaluations/2026-10-13-five-minute-cuda.md) for reference limits and the exact measurements.

**Version 0.5.3 has not been published to GitHub Releases.** The candidate's two local Windows build outputs are named:

- **Castwell-0.5.3-Windows-x64-Setup.exe**, for current-user installation with a Start menu shortcut. The default application directory is `%LOCALAPPDATA%\Programs\Castwell`.
- **Castwell-0.5.3-Windows-x64-Portable.zip**, for running after extraction. Extract the entire archive to a writable folder and open `Castwell\Castwell.exe`; keep the executable and `_internal` folder together.

The source is now licensed under [GNU AGPLv3 only](../LICENSE). The existing local candidates predate that change. Final public archives must be rebuilt with `LICENSE`, `NOTICE`, the completed third-party source inventory, updated instructions, and fresh checksums before publication.

Each completed build produces a manifest and `SHA256SUMS.txt` identifying its exact artifacts and source. Frozen runtime and installation checks are separate from the source episode benchmark; the benchmark alone does not validate an installer. [Published releases](https://github.com/mgelsinger/castwell/releases) can contain earlier versions, so check the version before comparing behavior.

These artifacts are unsigned. Windows may show an unknown-publisher or SmartScreen prompt. Compare the artifact's SHA-256 value with its accompanying checksum before running it.

The installer keeps the library in `%LOCALAPPDATA%\Castwell`, outside its application directory. The portable distribution uses a `Data` directory beside `Castwell.exe`. Both store episodes, transcripts, settings, listening state, and cut history in their library. Model weights can consume additional disk space.

To move a portable installation that uses its default library, stop Castwell and move the whole `Castwell` folder, including `Data`. The next launch resolves the default library beside the executable at its new location. A custom **Data folder** remains an absolute selection and does not move automatically. Keep any selected GGUF at its configured path, or choose its new location before starting.

## Start listening

Open Castwell from its Start menu shortcut or portable executable. Choose the **Data folder** if needed. For local ad detection, follow [download and select the tested classifier](#download-and-select-the-tested-local-classifier) below before selecting **Start**. The desktop launcher checks the GPU, starts the local service, and opens the podcast library in your browser. The service listens on loopback by default. Keep the launcher running while you use the library and use its stop or close control when finished.

1. In the library's settings, choose a Whisper model and select **Download model**. This explicit preparation downloads free model weights; the application does not silently download speech weights when an episode is queued. A new reviewed-Qwen setup defaults to `large-v3`, **Experimental fast CUDA**, speech batch size **8**, and mandatory manual review. Other new desktop profiles retain `small.en` and batch size 1. Existing and explicitly selected speech models remain unchanged. Select a multilingual model if your podcasts need it.
2. Add podcasts by RSS, import OPML, or upload recordings. Set **Keep original** or **Remove ads** for each show. Matching selections include episodes beyond the displayed catalogue page; batches of 50 or more ask for confirmation before preparation.
3. Prepare episodes in batches. Transcription uses CUDA and float16. **Keep original** retains ads and skips ad classification. **Remove ads** proposes cuts for manual review.
4. Use **Hear start / Hear end** to check each proposed boundary in the original audio. Each preview plays up to three seconds on either side at normal speed, stops automatically, and preserves your episode position. Stop the preview before marking boundaries from the episode playhead. Adjust and approve the selections you want, then render a cleaned listening copy. Original audio remains available.

Without speech weights, you can browse, download originals, import existing transcripts, and render manually reviewed cuts. Imported transcripts do not require speech recognition.

<a id="optional-local-classifier"></a>

## Download and select the tested local classifier

The classifier weights are a separate, free download. You do not need Python, Git, an API key, or a paid service. The desktop package already includes the llama.cpp CUDA runtime.

1. In your browser, [download the exact pinned Qwen3.8-27B Q4_K_M file](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/resolve/2c80088ea5e6033bed6a180e28a6573d98e8c0cf/Qwen3.8-27B-Q4_K_M.gguf). Save `Qwen3.8-27B-Q4_K_M.gguf` in a permanent folder, such as `D:\Models`. The file is **17,106,775,008 bytes**, approximately **17.1 GB**; allow additional space for speech weights and episodes.
2. Verify the completed download with the PowerShell commands below. Replace the example path with the location where you saved it. They only read the local file and use Windows' built-in tools.
3. In the desktop launcher, select **Browse** beside **Optional GGUF**. Select the verified file, then select **Start**. The launcher verifies it again, starts the local CUDA classifier, and reports `reviewed-qwen3.8-cuda` when ready. Keep the file at that location for future starts.

```powershell
$castwellModel = 'D:\Models\Qwen3.8-27B-Q4_K_M.gguf'
if ((Get-Item -LiteralPath $castwellModel).Length -ne 17106775008) {
    throw 'The model download is incomplete or is a different file.'
}
if ((Get-FileHash -LiteralPath $castwellModel -Algorithm SHA256).Hash -ne '7e78da5d7e3ae28d178121f58646953305f3e5bd3cb46f4a75584e8b6c6fe169') {
    throw 'The model checksum does not match the tested file.'
}
'Model verified.'
```

This SHA256 matches the [upstream file page](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/blob/2c80088ea5e6033bed6a180e28a6573d98e8c0cf/Qwen3.8-27B-Q4_K_M.gguf). The exact reviewed file uses a **32,768-token native context**. Other GGUF files use `generic-local-cuda` with an 8,192-token context; selecting them does not establish equivalent detection quality or enable the reviewed Qwen request profile. Choosing a file does not download it or spend money.

Run **Test AI connection** in the library's settings after startup. A passing connection test confirms that the service returns valid results; it does not establish ad-detection accuracy. Keep **Require my approval for every cut** enabled for either profile and review the proposed boundaries, especially jokes, unpaid parodies, and host reads.

The desktop package uses the selected local CUDA classifier through the launcher's own loopback service. Hosted classifier configuration and the older model comparison profiles are available through the separate [source checkout instructions](local-models.md#source-checkout-setup).

<a id="experimental-fast-settings-in-the-current-checkout"></a>

## Experimental fast settings

Version 0.5.3 includes **Experimental fast CUDA**. It reads the whole episode's context before checking the exact words at commercial boundaries. Its intended scope removes sponsor/product reads while preserving unpaid parody, editorial speech, and promotions for other free podcasts. The measured speed result applies to the tested configuration and recordings; it is not a guarantee for every episode or GPU.

With the exact reviewed Qwen3.8 artifact selected in the managed launcher, Settings offers **Scene and endpoint review** and **Experimental fast CUDA**. Fast is the default when no explicit scene/fast preference exists. A saved desktop preference takes precedence over a saved source policy; explicit scene/fast selections remain unchanged. Older libraries that relied on an implicit scene default move to fast with mandatory review. Other local GGUF files keep their existing profile and context. Hosted-provider settings remain stored separately from the desktop's local policy choice.

Fast mode forces **Require my approval for every cut**, including a result with no proposed cuts. Review the transcript and unresolved passages before saving decisions and exporting. The local episode directory includes a `fast-analysis.json` evidence file for troubleshooting. An empty proposal list does not prove that the recording has no ads.

The fast policy manages its own reasoning settings: 6,144 tokens for whole-episode reasoning and 1,024 for boundary reasoning, within respective total output caps of 8,192 and 2,048 tokens. The general reasoning checkbox is disabled for this policy. Each classifier request has a default 300-second timeout; that timeout is separate from the five-minute-per-audio-hour processing goal.

The separate **Speech batch size** setting accepts 1 through 16. Every saved batch value is preserved, including 1. With no saved batch value, the reviewed desktop uses 8 for fast and 1 for scene. The measured configuration used a 24 GB NVIDIA card. Model preparation remains explicit, inference remains CUDA-only, and queued retries retain the settings captured when the job was submitted. The source launcher's explicit `-FastProfile` flag instead saves batch size 8 each time it is used, as described in the [production guide](production.md#start-the-suite-on-windows).

The [architecture guide](https://github.com/mgelsinger/castwell/blob/main/docs/architecture.md#five-minute-episode-benchmark) describes the reproducible cold benchmark and what its accuracy scores establish.

## Earlier distributions

The published 0.5.1 detector and the 0.5.2 scene candidate are historical baselines. Their [earlier evaluation](evaluations/2026-10-12-ad-removal-system.md) failed the desired speed/accuracy combination. The 0.5.2 packages passed separate [runtime and installation checks](https://github.com/mgelsinger/castwell/blob/main/docs/evaluations/2026-10-13-windows-candidate.json), but predate the clarified policy preserving free podcast promos. Those results do not validate the new 0.5.3 artifacts or establish unattended removal.

## Open an existing library

An existing source-checkout library is not automatically imported. Stop its previous Castwell service and back up the library. Choose its directory using **Data folder** in the desktop launcher; the choice is saved after a successful start. An explicit launch directory takes precedence:

```powershell
& "$env:LOCALAPPDATA\Programs\Castwell\Castwell.exe" --data-dir "D:\Podcasts\My Castwell library"
```

For a portable copy, substitute its full executable path. Choose the library directory that contains `library.sqlite3` and episode directories. Do not run two Castwell processes against the same library. Keep the library and model files when updating the application.

To run only the local service, for a supervised deployment or troubleshooting:

```powershell
& "$env:LOCALAPPDATA\Programs\Castwell\Castwell.exe" --serve --data-dir "D:\Podcasts\My Castwell library" --port 8000
```

Open `http://127.0.0.1:8000`. See the [production guide](production.md) for backups, recovery, private access, and the full-podcast acceptance test.

## Update and uninstall

Close Castwell before replacing the portable files or running a newer installer. Keep portable `Data` and any separately stored model weights when replacing the application files. Installed libraries remain outside `%LOCALAPPDATA%\Programs\Castwell` and survive uninstalling the app. Back up your library before an upgrade.

Uninstall the installed application from Windows Settings or the Start menu uninstall shortcut. The uninstaller removes its executable, runtime, shortcuts, and uninstall registration. It preserves the user library and separately stored model weights. To remove personal data later, first verify the directory and delete it explicitly.

## Check a built distribution

The runtime check writes JSON without opening the browser:

```powershell
& ".\Castwell\Castwell.exe" --check-runtime --report ".\runtime-check.json"
```

Its report includes bundled binary paths, required engine imports, NVIDIA GPU information, float16 support, and GPU allocation results. A successful runtime check establishes prerequisites; test a real full episode before relying on detection results.

Maintainers can exercise both artifacts with the standard-library Windows smoke harness:

```powershell
python scripts/smoke_windows_distribution.py `
  --portable "dist\windows\Castwell-0.5.1-Windows-x64-Portable.zip" `
  --installer "dist\windows\Castwell-0.5.1-Windows-x64-Setup.exe" `
  --speech-model "D:\Models\faster-whisper-small.en" `
  --speech-audio "D:\Test audio\short-speech.wav" `
  --artifacts ".local\windows-distribution-check"
```

The harness runs the frozen applications with external Python and FFmpeg removed from PATH. It uses a temporary path containing spaces, verifies UI and diagnostics, downloads a local RSS fixture in a batch, renders a known manual cut with real FFmpeg, verifies ZIP contents, and runs cached-model CUDA transcription. It moves a stopped portable installation and checks that its adjacent library, original and cleaned audio, transcript, reviewed cuts, and listening state survive reopening. It also checks current-user installation, shortcut targets, uninstall registration, graceful shutdown, and library preservation. It refuses to replace an existing Castwell installation or Start menu group. It does not download weights or contact classifier APIs, and the supplied speech recording is not copied into public artifacts.

On a CI machine without a supported NVIDIA GPU, use `--expect-no-gpu` and omit both speech arguments. That mode verifies clear CUDA rejection, bundled files, installation, shortcuts, and uninstall preservation. It does not establish GPU inference or full application readiness.
