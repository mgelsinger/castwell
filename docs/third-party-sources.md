# Windows dependency sources and notices

The 0.5.5 candidate replaces the previous prebuilt media components with controlled builds from pinned sources. The separate `Castwell-0.5.5-Dependency-Sources.zip` supplies the reviewed source archives, build recipes, exact PyAV version patch, checksums and supplemental notices. It accompanies the portable app, installer and separate Castwell application source ZIP. Users do not need to download the sources to run Castwell.

The [inventory](third-party-sources.json) and [source lock](../packaging/windows-sources.lock.json) distinguish source collection from the remaining public release conditions. A checksum verifies the recorded bytes; it does not independently certify legal clearance, security or ad-removal accuracy. Castwell remains AGPL-3.0-only. Dependencies retain their own terms.

## What the new media build contains

FFmpeg 8.1.2 is built from its verified upstream release, with LAME 3.100, Ogg 1.3.6, Vorbis 1.3.7 and Opus 1.6. The profile disables optional GPL/nonfree codecs, network protocols and automatic discovery of unrelated libraries. It supports the application's local audio formats, trimming, concatenation and resampling. MP4 input is decoded for its audio; exports contain audio only. CUDA speech and classification remain separate.

The archive retains the full upstream media sources, LGPL and permissive notices, the pinned build recipe, the w64devkit toolchain source archive and recipe, and NASM sources. The recipe produces static command-line tools and shared libraries for PyAV. It makes no changes to the upstream codec sources. Compiler executables are acquired separately using the pinned URLs and hashes. No non-Windows compiler runtime DLL is required by these media binaries.

PyAV 16.1.0 is rebuilt as `16.1.0+castwell1` against those shared libraries. Its only source patch changes the version metadata, and that exact patch is in the PyAV lock. The recipe pins MSVC tools, Python build tools and delvewheel's DLL import rewriting. PyAV retains its BSD-3-Clause terms; its seven bundled FFmpeg DLLs retain their separate licenses. The recipes permit rebuilding and relinking. Byte-identical output across different build hosts has not been established and is not claimed as a source-delivery requirement.

The [media checks](evaluations/2026-10-15-controlled-media.json) passed all seven export formats, sample-exact WAV splices, eight input formats and video-containing MP4 input. The [PyAV comparison](evaluations/2026-10-15-controlled-pyav-pcm.json) produced identical 16 kHz float32 transcription input for both complete cached episodes and seven synthetic cases. These are decoder compatibility results, not new ad-classification scores or listening observations.

## Other dependencies

The source ZIP includes exact certifi and Eigen sources for their MPL obligations. The certifi certificate data matches the shipped file byte for byte; the Eigen revision and archive hash match ONNX Runtime's pinned dependency. Optional llama.cpp and CTranslate2 source snapshots are identified separately. The CTranslate2 snapshot omits Git submodules and is not described as a complete standalone build tree.

Each locked wheel's available license files and metadata are retained in `THIRD-PARTY-NOTICES/python`. Supplemental Tcl, OpenSSL, Intel OpenMP, oneMKL, oneDNN and llama.cpp dependency notices are preserved verbatim. CPython, Tk, libffi, NumPy, ONNX, LLVM, CUDA, cuDNN and the installer retain their applicable notices. PyInstaller's bootloader exception and NumPy's GCC Runtime Library Exception were reviewed separately; using those build/runtime components is not treated as a requirement to publish proprietary NVIDIA or Microsoft sources.

The Microsoft runtime is now pinned to the official 14.44.35211 release. Its exact DLL hashes and source VSIX are recorded. The freezer excludes inherited application folders from its DLL search, and checks every collected copy of the selected Microsoft runtime. This prevents an unrelated desktop app's runtime from silently entering the package.

## Rebuild and source access

Follow [the Windows build instructions](windows.md). The source collector verifies the inventory and repository recipes, downloads only pinned public archives when requested, and checks the complete ZIP against its manifest. The Windows builder also compares the source inventory's binary bindings to the actual frozen application. A changed source, missing artifact, stale lock, or mismatched binary fails the check.

To use the dependency archive directly, extract it and keep the `sources` layout. The `sources/media` files can populate the media recipe's archive cache; the PyAV source is in `sources/pyav`. Copy the retained `scripts` and `packaging` files into a matching Castwell checkout, then follow the recorded recipes. The PyAV recipe obtains its separately pinned build-tool wheels. MSVC and Windows SDK installation remain external prerequisites.

When public distribution is ready, publish this exact source ZIP and the exact application source ZIP alongside the matching binaries, and retain their checksums and access. A broad upstream homepage link or the application ZIP alone does not supply the bundled dependency sources. FFmpeg's [licensing guidance](https://ffmpeg.org/legal.html) explains the corresponding-source and external-library considerations.

## Remaining public release conditions

The local source collection is complete for the reviewed inventory. **Public binary release remains held.** The free Visual Studio Community 2022 route supplies a qualifying product license for this open-source development use and has express redistribution provisions, subject to its terms. This machine currently has Build Tools; that installation and the runtime's end-user notice were not treated as proof of a distributor grant. The verified Community installer was prepared but not launched because this session lacks administrator rights. Completing that setup and qualifying tool use requires a Windows administrator approval. See the [Community terms](https://visualstudio.microsoft.com/license-terms/vs2022-ga-community/), [REDIST list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution) and [installer policy](https://learn.microsoft.com/en-us/visualstudio/install/configure-policies-for-enterprise-deployments?view=visualstudio).

Package tests and independent audible acceptance remain separate gates. The replacement decoders do not establish that the detector meets the agreed residual-ad and show-loss targets. Manual review remains enabled, and no production go-live claim follows from completing the source archive.

The [October 14 vendor inventory](evaluations/2026-10-14-third-party-sources.json) remains historical evidence for 0.5.4 and earlier. Those archives are unchanged. The new controlled binaries and source bundle do not retroactively resolve the old vendor packages' source gaps.
