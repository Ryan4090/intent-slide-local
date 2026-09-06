# Portable LibreOffice renderer review

Review date: 2026-09-06. Maintainer packaging scope: the pinned LibreOffice 26.8.0 binary distribution, build version 26.8.0.3. This component supplies local PPTX-to-PDF rendering without asking a user to install LibreOffice. Intent-Slide is a separate product; The Document Foundation does not endorse this repackaging.

## Decision and boundaries

The selected distribution is the same official stable release for macOS arm64, macOS x64, and Windows x64. The original application tree and its notices are retained. Windows packaging additionally copies ten unchanged x64 runtime DLLs from the same MSI's `System64/` directory into `program/`; the manifest records both paths and matching hashes. The maintainer script changes archive ownership/timestamps for deterministic packaging; it does not modify executable bytes, add fonts inside the signed application, re-sign, install services, register file associations, or launch LibreOffice.

The native macOS Quick Look alternative requires platform-specific tooling and does not supply Windows rendering. A separately installed LibreOffice would violate the clone-only product requirement. The official PortableApps package is an alternative for Windows, but the reviewed MSI administrative image avoids introducing another unpacker and another release/version boundary. The official download page describes the available distributions and the separately maintained PortableApps option. [Downloads](https://www.libreoffice.org/download/), [other distributions](https://www.libreoffice.org/download-other/).

## Exact upstream artifacts

All three installers were downloaded into the repository's ignored `.runtime/portable-renderer-build/downloads/` directory. Their actual byte counts and SHA-256 values match the pinned official `.sha256` responses. Mirrors are transport endpoints; the manifest retains the canonical upstream URL. A matching checksum is integrity evidence, not a malware audit or proof that the upstream server was uncompromised.

| Platform | Official artifact | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| macOS arm64 | [26.8.0 DMG](https://download.documentfoundation.org/libreoffice/stable/26.8.0/mac/aarch64/LibreOffice_26.8.0_MacOS_aarch64.dmg) | 298773447 | `8858d8058da4f862f47559486814e65efc27294da67c5e4bb56b006b1ee59f89` |
| macOS x64 | [26.8.0 DMG](https://download.documentfoundation.org/libreoffice/stable/26.8.0/mac/x86_64/LibreOffice_26.8.0_MacOS_x86-64.dmg) | 309257813 | `2dcbce4894e01bc1ecd594658e2cbda70ff7bfcd0b310f35d38887797172d09e` |
| Windows x64 | [26.8.0 MSI](https://download.documentfoundation.org/libreoffice/stable/26.8.0/win/x86_64/LibreOffice_26.8.0_Win_x86-64.msi) | 374906880 | `4aa6c6e1895f4055104effcb556bd3362d20c6ad707c149543304f395ef9db95` |

The checksum URL for each row is the artifact URL with `.sha256` appended. [Machine-readable manifest](../../vendor/portable/renderer-manifest.json) contains the archive and every <=90 MiB part's own byte count and SHA-256. The archive hash differs from the installer hash because the archive contains the extracted application tree.

## Packaging and executable entry points

`scripts/build_portable_renderer.py` is a maintainer-only standard-library Python script. The end-user launcher does not invoke it or download from the internet. The script's only network calls are explicit `--download` / `--build` retrievals from the pinned upstream release, using HTTPS with HTTPS-only redirects. A changed checksum, excess size, special file, setuid/setgid mode, outside symlink, missing notice, missing executable, or changed existing output fails packaging.

macOS extraction uses `hdiutil attach -readonly -nobrowse -noautoopen` at an explicit repository cache mountpoint. It copies the original `LibreOffice.app`, validates its signature, and detaches the image. The resulting entry is `LibreOffice.app/Contents/MacOS/soffice`. Both distributions report build `26.8.0.3` and minimum system `11.0.0` in their original Info.plist; that metadata alone does not prove compatibility on every supported system.

Windows extraction uses a Windows CI administrative image, with `msiexec /a <verified.msi> /qn TARGETDIR=<temporary extraction path> /L*v <log>`. The complete resulting application directory must be passed as `--windows-tree` inside the repository cache; the maintainer must first establish its provenance against the official MSI. The entry is `LibreOffice/program/soffice.exe`; MSI files and extraction logs are not part of that root. Microsoft documents `/a` as an administrative installation and `/qn` as no UI. [Microsoft msiexec reference](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/msiexec).

For this release, [Windows CI run 34028687501](https://github.com/Ryan4090/intent-slide-local/actions/runs/34028687501) reported administrative-extraction exit 0 from the pinned MSI. Its input tar was independently checked as 508287657 bytes / SHA-256 `c13a3051d54e888263310ae244a8e7b7ef0764f1a98f1927730d957fa7e636db`. The original 19,418 regular files are preserved, and ten program-local DLL copies must match their original `System64` counterparts. Its entry is a PE x86-64 binary. The workflow did not perform Authenticode verification or run the renderer; these remain separate checks. [Preserved CI provenance](../../vendor/portable/renderer/win32-x64/ci-provenance.json).

### Windows application-local C++ runtime

The original `program/soffice.exe` PE import table directly requires `MSVCP140.dll`, `VCRUNTIME140.dll`, and `VCRUNTIME140_1.dll`; `mergedlo.dll` also imports the `MSVCP140_1` and `_2` variants. The administrative image initially placed these files in the sibling `System64` directory. The executable's embedded manifest does not redirect DLL probing there. Merely retaining this sibling directory would leave a clean Windows machine dependent on an unrelated system installation. Microsoft's documented search order includes the executable directory but does not recursively search its parent's subdirectories. [DLL search order](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order).

The `app-local-v1` packaging revision copies the ten pinned, PE-machine `0x8664` DLLs from `System64` beside `soffice.exe`, retains every source file, and rejects absent sources, unexpected hashes, architecture mismatch, links, or conflicting target bytes. Microsoft documents this application-local placement as supported and leaves updates to the distributor. The executable and DLL payload bytes are unchanged. [Application-local deployment](https://learn.microsoft.com/en-us/cpp/windows/walkthrough-deploying-a-visual-cpp-application-to-an-application-local-folder?view=msvc-170), [DLL selection and servicing](https://learn.microsoft.com/en-us/cpp/windows/determining-which-dlls-to-redistribute?view=msvc-170).

UCRT and the `api-ms-win-crt-*` API sets are OS components on Windows 10 and later; the bundle does not add or install its own `ucrtbase.dll`. Selected import tables also refer to normal OS DLLs, including Media Foundation. Old Windows versions and reduced OS editions are not established as supported by this static review. A fresh native Windows run and its loaded DLL paths remain necessary execution evidence. [Microsoft UCRT deployment](https://learn.microsoft.com/en-us/cpp/windows/universal-crt-deployment?view=msvc-170).

The existing LibreOffice `license.txt` contains the Microsoft Visual C++ Runtime Libraries section. Its installation/use clause allows any number of copies, and its backup clause allows one backup; it also reserves other rights and includes sharing/transfer restrictions. This review does not interpret those end-user clauses as a separate unrestricted redistribution grant. Microsoft's redistribution documentation states licensing conditions for distributors. We preserve the original notice, reuse only the same official LibreOffice MSI payload, and neither download SDK/system DLLs nor add a Visual Studio installation prerequisite for recipients. Independent confirmation of the distributor's applicable Microsoft rights remains **UNVERIFIED**. The LibreOffice/MPL notice does not replace Microsoft's separate terms. [Microsoft redistribution conditions](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170).

Only the application root is archived. Internal relative symlinks are retained and checked for confinement. Every ordinary tar member's bytes were compared with its original copied Mac file; original nested LICENSE/NOTICE/COPYING/copyright files remain included. Extra plain-text copies of the top-level notices are next to the archive parts for easier inspection.

## Licenses and corresponding source access

The product's original LICENSE identifies MPL-2.0 plus separate component terms. Its contents include LGPL, GPL, Apache, BSD/MIT-style, SIL OFL and other notices; the entire bundle must not be described as solely MPL-2.0. The original unmodified LICENSE and NOTICE are retained in every application archive. [LibreOffice's official licensing explanation](https://www.libreoffice.org/licenses/).

The MPL executable distribution condition requires recipients to be told how to obtain corresponding source, and their source rights must remain intact. Recipients can obtain the exact upstream source release from the following official links. These URLs and their published SHA-256 values were retrieved; this review did not rebuild the executable or prove a reproducible source-to-binary match. [Official source release listing](https://www.libreoffice.org/download-other/).

| Source archive | Published SHA-256 |
| --- | --- |
| [libreoffice-26.8.0.3.tar.xz](https://download.documentfoundation.org/libreoffice/src/26.8.0/libreoffice-26.8.0.3.tar.xz) | `42116e256933aa575974e420ffa04f7cd7096f4b7ca5d0907ddeaf2a07f68f94` |
| [libreoffice-dictionaries-26.8.0.3.tar.xz](https://download.documentfoundation.org/libreoffice/src/26.8.0/libreoffice-dictionaries-26.8.0.3.tar.xz) | `4849ca14733cf4d5896a2f5bb6db87ffade9d1fbeab92e63153dc79630995a60` |
| [libreoffice-help-26.8.0.3.tar.xz](https://download.documentfoundation.org/libreoffice/src/26.8.0/libreoffice-help-26.8.0.3.tar.xz) | `8443f21b7127cbd084b472c01ff494ec6a31814f0b1b382004b68103ece90e35` |
| [libreoffice-translations-26.8.0.3.tar.xz](https://download.documentfoundation.org/libreoffice/src/26.8.0/libreoffice-translations-26.8.0.3.tar.xz) | `dc1419fc6f02840735b01f28b1cd453885e50fad2b2b2f5e6beab93868bf82a4` |

Preserving notices and linking the release sources is verified here. Complete corresponding-source coverage for every bundled third-party component, ongoing source availability, and each GPL/LGPL redistribution condition are **PARTIALLY_VERIFIED**; this packet is not a claim of a complete legal or source audit. A release maintainer must keep the source-access notice with the binary distribution and review additional component obligations before changing or separately redistributing components. No new license or warranty is imposed on these original files.

## Font decision

No Pretendard files were inserted into either signed Mac application. The repository's existing Pretendard license is SIL OFL 1.1 and requires its copyright and license to accompany redistribution. Its presence elsewhere in the repository does not prove that LibreOffice loads it.

The exact 26.8.0.3 source scans application-relative font directories; Quartz registers these fonts using `kCTFontManagerScopeProcess`, and Windows uses `FR_PRIVATE`. This verifies that those specific registrations are process-private. It does not prove that an arbitrary external font directory or an undocumented environment variable is honored on Mac. [Quartz source](https://github.com/LibreOffice/core/blob/libreoffice-26.8.0.3/vcl/quartz/salgdi.cxx), [Windows font source](https://github.com/LibreOffice/core/blob/libreoffice-26.8.0.3/vcl/win/gdi/salfont.cxx).

Mac font injection would change sealed resources and is deliberately absent. Korean text, math glyph coverage, substitution, and per-page rendering must be verified separately against the actual runtime; this package review does not claim those visual results.

## Runtime, permissions and opaque boundaries

This is a large native document processor with bundled libraries, Python, extensions, import filters and scripts. Original code-signature checks validate signed resources, not behavior or vulnerability absence. No full binary disassembly, malware analysis, dependency CVE audit, or source rebuild was performed. Network, update and persistence behavior of every included component is **UNVERIFIED**. Intent-Slide should call the fixed local `soffice` entry with a per-job profile and bounded input/output paths; it should not launch installers, extension managers or auto-updaters.

The original Windows `program/version.ini` contains the upstream update and extension-update URLs; this establishes configuration, not observed network traffic. The installed registry was parsed without modification: `/org.openoffice.Office.Update` has boolean `Enabled` (default true, documented there as conditional on MAR updater support), and `/org.openoffice.Office.Jobs/Jobs/UpdateCheck/Arguments` has `AutoCheckEnabled` (true) and `AutoDownloadEnabled` (false). The latter `onlineupdate.xcd` file is identical in the reviewed Mac arm64 and Windows images (SHA-256 `2e9dc944fab52138a62273a2481e6c2a92fd53da69638edc6f82a28ab86c7390`). A per-job profile can request false for these keys without changing the signed distribution; confirming that the runtime honors them and emits no update traffic requires separate execution evidence.

No system install, privilege escalation, service registration, browser control or user font installation was performed by this packaging task. An isolated LibreOffice profile is not an OS sandbox. In particular, the Mac source's `create_SalInstance` removes `restorecount.plist` and `restorecount.txt` under the current user's LibreOffice saved-application-state directory. Therefore, a separate `UserInstallation` argument alone does not justify a claim that all native writes are repository-local. [Exact tagged Mac initialization source](https://github.com/LibreOffice/core/blob/libreoffice-26.8.0.3/vcl/osx/salinst.cxx).

## Verification record

| Claim | State | Evidence / limit |
| --- | --- | --- |
| Three original installer byte counts and SHA-256 values | VERIFIED | Downloaded bytes match the pinned official checksums. |
| Mac application signatures before and after tar extraction | VERIFIED | Both original copied apps and both `tarfile` data-filter roundtrip extractions pass `codesign --verify --deep --strict --verbose=2` (exit 0); publisher is The Document Foundation, Team `7P5S3ZLCN7`. Gatekeeper/notarization admission was not tested. |
| Archive content and part reassembly | VERIFIED | 17,449 regular files per Mac architecture and 19,428 Windows files checked by hash: all 19,418 Windows originals are unchanged and ten added DLLs match the original System64 payload; 4 + 4 + 6 parts; complete reassembled hashes and sizes match. |
| Original license notice preservation | VERIFIED | 65 notice-like files per Mac archive and 59 Windows files indexed and retained; complete top-level LICENSE/license.txt and NOTICE copied unchanged. |
| Builder rejection/roundtrip behavior | VERIFIED | 14 synthetic tests passed: internal/outside links, notice/entry absence, privileged mode, deterministic content, changed archive, corruption, part order, original app-local copies/idempotency, wrong architecture/hash, target collision and link rejection. |
| Windows extracted application provenance | VERIFIED | CI administrative extraction exit 0; pinned MSI and incoming CI archive integrity verified; every original file matches the CI archive and every added program-local DLL matches its original source. The manifest records the ten source/target SHA mappings. |
| Windows app-local C++ runtime placement | VERIFIED | All ten copied DLLs have the pinned MSI SHA and x64 PE machine; original executable bytes remain unchanged. Five targeted regression checks cover copy/idempotency, wrong architecture, wrong hash, collision and links. Native clean-machine startup remains unverified. |
| Windows Authenticode | UNVERIFIED | The extraction workflow and this Mac packager did not validate Windows signatures. A PE header check is not signature validation. |
| Native rendering and clean-machine launch | UNVERIFIED | This packaging task has not launched the native renderer. Separate integration evidence must identify OS/architecture and actual render output. |
| Full binary safety / corresponding-source audit | PARTIALLY_VERIFIED | Published origins, checksums, notices and selected source paths reviewed; opaque binary behavior and complete third-party source coverage remain outside this review. |
| Rollback | UNVERIFIED | Generated vendor parts and ignored cache can be removed/reverted as local artifacts; this rollback procedure was not exercised. |

Maintainer commands (use an existing project Python):

```text
python -B scripts/build_portable_renderer.py --download
python -B scripts/build_portable_renderer.py --build --platform darwin-arm64 --platform darwin-x64
python -B scripts/build_portable_renderer.py --build --platform win32-x64 --windows-tree .runtime/portable-renderer-build/windows-ci/LibreOffice
python -B scripts/build_portable_renderer.py --self-test
```

The script does not overwrite different existing archive parts. Adopting a reviewed replacement in an existing manifest requires explicit `--replace-platform <platform>`; the Windows runtime fix uses the new `app-local-v1` archive name. Its superseded archive and six parts were preserved in the ignored renderer-build cache. The Mac archives and their manifest entries did not change. Updating the pinned release requires a new component review and an explicit version change. Raw maintainer logs and installers stay under the ignored repository cache; no credentials, private project documents, browser session data, or user profile were included in the renderer bundle.

Final archive inventory:

| Platform | Archive bytes | Parts | Archive SHA-256 |
| --- | ---: | ---: | --- |
| macOS arm64 | 292802328 | 4 | `16a836aa85e2c88fcae99021673e5b0fbc93e8da256f7540f4a4833dfccf3cf6` |
| macOS x64 | 303105785 | 4 | `4f8fc25c25396ff624f4de2ab2c9fb2ed6587c4f81ae9301d75c06a0da147a65` |
| Windows x64 · app-local-v1 | 506763047 | 6 | `f04e94e9e4227dd8ae593224af6ab403f774dedb82cbb67adb51a8f92b1bed14` |

[Public packaging evidence](../../vendor/portable/renderer/packaging-verification.json), [raw synthetic test result](../../vendor/portable/renderer/packaging-tests.log), [Windows dependencies and source-notice boundary](../../vendor/portable/renderer/win32-x64/dependency-review.json), and [source-access metadata](../../vendor/portable/renderer/source-access.json) accompany the manifest. No independent second-agent code review is claimed here; archive verification is independently performed from the builder's write stream.
