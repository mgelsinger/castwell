# Microsoft development setup completed

The previously pending developer setup was completed on October 17, 2026. Visual Studio Community 2022 and the required C++ compiler/Windows SDK were installed automatically under the owner's explicit authorization. The installer returned success with no restart required. The owner does not need to select workloads or run developer setup commands.

| Check | Result |
| --- | --- |
| Official installer identity | Prepared SHA256 and valid Microsoft signature verified before execution |
| Installation | Community 2022 version 17.14.42, complete and launchable |
| Compiler selection | Explicit Community path; no fallback to the older Build Tools installation |
| Controlled dependency build | Existing PyAV recipe completed offline using cached pinned sources and tools |
| Isolated import | Custom PyAV wheel and its controlled FFmpeg libraries loaded successfully |
| Reboot | Not required or performed |
| Model inference / paid API calls | Zero |

The [machine-readable record](2026-10-17-microsoft-setup.json) preserves installation and build identities. The Community installation provides the previously missing product setup; the actual dependency rebuild records use of those tools for Castwell. Microsoft's [Community terms](https://visualstudio.microsoft.com/license-terms/vs2022-ga-community/) and [redistribution list](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution) remain applicable. This is an installation/build record, not blanket legal certification.

## Existing Castwell downloads

The 0.5.5 portable app, installer and matching source archives already exist and passed their [CUDA package checks](2026-10-15-windows-source-candidate.md). Their exact hashes and frozen application revision are unchanged and were checked again after setup. No additional app installer is required to complete this developer setup step.

The new Community compiler is servicing version 19.44.35229 within the pinned 14.44.35207 toolset directory. The earlier package used compiler 19.44.35228. The fresh verification build therefore remains separate: its 49 compiled extensions differ, while all seven controlled media DLLs match. It was not substituted into the validated app or source archive. Promoting that new wheel in a future release would require new receipts, binary bindings and package validation.

The dated October 15 reports and bundled source guide retain their original setup-pending statements as historical build evidence. This addendum supersedes that setup status. The delivery index now records the completed setup alongside the unchanged package hashes.

## Remaining acceptance work

Independent listening is still needed to check real sponsor coverage, preserved show speech and parody, followed by a fresh complete-episode acceptance run. Developer setup does not add ad-removal accuracy or speed evidence. Manual review remains enabled, production go-live is unconfirmed, and public binary/source publication has not occurred.
