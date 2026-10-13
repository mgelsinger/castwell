# Windows desktop installation

The Windows x64 distribution includes Castwell's desktop launcher, Python runtime, FFmpeg, the Whisper transcription engine, and a CUDA build of llama.cpp. Install Python or FFmpeg separately only if you choose to run the source checkout. Speech and classifier weights are separate downloads.

Local inference requires a supported NVIDIA GPU and an NVIDIA driver compatible with the bundled CUDA runtime. Castwell checks CUDA, float16 support, and a real GPU allocation before starting. It reports an unavailable GPU or incompatible driver and stops instead of falling back to CPU inference. FFmpeg remains available for audio preparation and rendering.

## Choose a distribution

Version **0.5.2 is a manual-review test candidate**, with a faster scene detector and stricter batch completion checks. Its fresh ad-removal accuracy gate failed. Its files are `Castwell-0.5.2-Windows-x64-Setup.exe` and `Castwell-0.5.2-Windows-x64-Portable.zip`. Review every proposed cut and unresolved speech passage before exporting. See the included [evaluation](evaluations/2026-10-12-ad-removal-system.md) and [remaining work](ad-removal-production-plan.md). Neither this candidate nor 0.5.1 establishes safe unattended removal.

Get the Windows assets from [Castwell releases](https://github.com/mgelsinger/castwell/releases/latest):

- **Castwell-0.5.1-Windows-x64-Setup.exe** installs for the current Windows user and creates a Start menu shortcut. The default application directory is `%LOCALAPPDATA%\Programs\Castwell`.
- **Castwell-0.5.1-Windows-x64-Portable.zip** runs after extraction. Extract the entire archive to a writable folder and open `Castwell\Castwell.exe`; keep the executable and `_internal` folder together.

These artifacts are unsigned. Windows may show an unknown-publisher or SmartScreen prompt. Check that the file came from the linked project release and compare its SHA-256 value with the release checksum before running it.

The installer keeps the library in `%LOCALAPPDATA%\Castwell`, outside its application directory. The portable distribution uses a `Data` directory beside `Castwell.exe`. Both store episodes, transcripts, settings, listening state, and cut history in their library. Model weights can consume additional disk space.

To move a portable installation that uses its default library, stop Castwell and move the whole `Castwell` folder, including `Data`. The next launch resolves the default library beside the executable at its new location. A custom **Data folder** remains an absolute selection and does not move automatically. Keep any selected GGUF at its configured path, or choose its new location before starting.

## Start listening

Open Castwell from its Start menu shortcut or portable executable. Choose the **Data folder** if needed, optionally choose a local classifier, then select **Start**. The desktop launcher checks the GPU, starts the local service, and opens the podcast library in your browser. The service listens on loopback by default. Keep the launcher running while you use the library and use its stop or close control when finished.

1. In the library's settings, choose a Whisper model and select **Download model**. This explicit preparation downloads free model weights; the application does not silently download speech weights when an episode is queued. The published 0.5.1 package defaults to `small.en`. The 0.5.2 candidate defaults a new library to `large-v3` when the exact pinned Qwen profile is selected; other new desktop profiles retain `small.en`. Existing and explicitly selected speech models remain unchanged. Select a multilingual model if your podcasts need it.
2. Add podcasts by RSS, import OPML, or upload recordings. Set **Keep original** or **Remove ads** for each show. Matching selections include episodes beyond the displayed catalogue page; batches of 50 or more ask for confirmation before preparation.
3. Prepare episodes in batches. Transcription uses CUDA and float16. **Keep original** retains ads and skips ad classification. **Remove ads** proposes cuts for manual review.
4. Listen around each proposed cut, adjust the boundaries, and approve the selections you want. Render approved cuts to make a cleaned listening copy. Original audio remains available.

Without speech weights, you can browse, download originals, import existing transcripts, and render manually reviewed cuts. Imported transcripts do not require speech recognition.

## Optional local classifier

Use the desktop launcher's local-model controls to select an existing compatible GGUF file and start the bundled llama.cpp service. The local classifier uses the NVIDIA GPU. Weight files are not part of the installer, and choosing a file does not download it or spend money. Model size must fit the available GPU memory and the selected context.

The launcher verifies the selected file. The exact pinned **Qwen3.8-27B Q4_K_M** from the [reviewed profile](https://github.com/mgelsinger/castwell/blob/main/docs/local-models.md#qwen38-27b-reviewed-profile) automatically uses `reviewed-qwen3.8-cuda`. The published 0.5.1 package uses a 1,024-token reasoning budget and a 4,096-token output cap. The 0.5.2 candidate uses a nonthinking compact sweep, then focused boundaries and endpoints with separate 256-token reasoning budgets; it defaults to manual approval because ad-removal acceptance has not passed. Other GGUF files use `generic-local-cuda`; selecting them does not establish equivalent detection quality or enable the reviewed Qwen request profile. The launcher reports the selected profile when ready.

Run **Test AI connection** in the library's settings after startup. A passing connection test confirms that the service returns valid results; it does not establish ad-detection accuracy. Keep **Require my approval for every cut** enabled for either profile and review the proposed boundaries, especially jokes, unpaid parodies, and host reads.

The desktop package uses the selected local CUDA classifier through the launcher's own loopback service. Hosted classifier configuration remains available when running Castwell from source; it is outside the packaged desktop workflow.

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
