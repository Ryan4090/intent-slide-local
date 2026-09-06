# Portable runtime component review

Review date: 2026-09-06. Status: **PARTIALLY_VERIFIED — artifacts and native macOS ARM64 runtime verified; other hosts require native CI**.

The user requested a clone that can run without separately installing Python, Node,
an AI CLI, or runtime libraries. This is authorization for the reviewed repository-local
bundle. No credentials, existing virtual environment, global tool, user configuration,
Claude binary, or third-party account data is copied. Publication is a separate step.

## Fixed sources and benefit

| Component | Exact reviewed source | Rights and capability |
| --- | --- | --- |
| CPython | Python 3.12.14, [Astral python-build-standalone 20260901](https://github.com/astral-sh/python-build-standalone/releases/tag/20260901), official immutable release asset SHA-256 | Portable interpreter; PSF and bundled dependency licenses remain in the original archive. Astral's MPL-2.0 build-system license is preserved separately. |
| Codex | [OpenAI 0.153.4](https://github.com/openai/codex/tree/rust-v0.153.4), official npm platform archives, exact SHA-512 SRI | Unmodified CLI and code-mode helper; [Apache 2.0 source license](https://github.com/openai/codex/blob/rust-v0.153.4/LICENSE) preserved. User authenticates through the original CLI. |
| Python wheels | Existing `requirements-local.lock`, unchanged versions/hashes, official PyPI wheels for Python 3.12 | Only the already locked 23 packages. Native wheels and their included license members are retained without repacking. |

The portable archives cover macOS ARM64, macOS x86-64 and Windows x86-64. Compiled
binary behavior is not proven by metadata or a hash. Actual host tests are reported
separately; Windows execution must not be inferred from macOS fixtures.

## Permissions, integrity and updates

- `scripts/build_portable_runtime.py --build` is an explicit maintainer network
  operation. Startup must never call it or download missing components.
- The builder reads public package metadata and downloads archives. It performs
  no package lifecycle scripts, source builds, global installation, privilege
  changes, persistent hooks, credential reads, or automatic updates.
- Python asset hashes are pinned from the official release API. Codex platform
  payloads are verified against pinned npm SRI. Wheels use `pip download
  --isolated --require-hashes --only-binary=:all:` and are checked against the
  original dependency manifest again after download.
- Archive member names, link targets, required regular entrypoints and bounded
  license members are inspected without running them. Repeated empty directory
  entries in MuPDF's official source tar are recorded; duplicate files are rejected.
- Each distributed file is bound to SHA-256 and size in `runtime-manifest.json`.
  Archives above 90 MiB are divided into ordered byte chunks. Concatenating those
  chunks must reproduce the exact original archive SHA and length before extraction.
  This is not a rebuild or change to the official executable.
- Python and CLI extraction, offline wheel setup, and host launch are owned by
  the separate bootstrap. It must reject missing or changed files, never fetch
  replacements, and preserve native authentication and provider permission gates.

## License boundary

The application's MIT license does not relicense bundled components. In particular,
[PyMuPDF and MuPDF have AGPL or commercial terms](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).
Their COPYING and wheel-contained notices are preserved. The following unchanged
official sources, build instructions and license texts are also delivered in the
clone; users do not need to follow an external link to obtain these source bytes.

| Source payload | SHA-256 | Included build instructions |
| --- | --- | --- |
| `vendor/portable/sources/pymupdf-1.28.2.tar.gz` (87,903,557 bytes) | `5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249` | `pymupdf-1.28.2/setup.py`, `pyproject.toml`, `scripts/gh_release.py`, `COPYING` |
| `vendor/portable/sources/mupdf-1.28.2-source.tar.gz` (68,898,646 bytes) | `44075a84e329db55b9bef5f342a70fd26d69e48ad1d33cb89d9664581c641156` | `mupdf-1.28.2-source/Makefile`, `Makerules`, `Makethird`, `scripts/mupdfwrap.py`, `setup.py`, `pyproject.toml`, `COPYING` |

**VERIFIED:** the [PyPI 1.28.2 sdist metadata](https://pypi.org/pypi/PyMuPDF/1.28.2/json)
binds the first source hash. Its `setup.py` selects MuPDF 1.28.2, but the sdist
does not contain MuPDF itself. Therefore the second source archive is included
separately, verified against the [Artifex 1.28.2 release asset digest](https://api.github.com/repos/ArtifexSoftware/mupdf-downloads/releases/tags/1.28.2).
All three wheels' included `mupdf/fitz/version.h` declare 1.28.2; the native macOS
wheel also reports both PyMuPDF and MuPDF version 1.28.2 at runtime. MuPDF's
source includes its third-party source tree and 34 license/notice members.

**UNVERIFIED:** a bit-for-bit rebuild of the published wheels and complete legal
compatibility of a distribution combining these components have not been proved.
Source build tools (including the upstream `pipcl` backend, compilers and SWIG)
are not installed or run by this product. The bundled wheels make end-user
startup independent of a source build. No commercial license purchase is made.

The Codex platform archives omit standalone license text. The bundle therefore
includes the exact tagged OpenAI Apache license and [NOTICE](https://github.com/openai/codex/blob/rust-v0.153.4/NOTICE)
plus notices for its unchanged ripgrep 15.2.0 and zsh helper. The helper versions
come from the [tagged ripgrep manifest](https://github.com/openai/codex/blob/rust-v0.153.4/scripts/codex_package/rg)
and [zsh manifest](https://github.com/openai/codex/blob/rust-v0.153.4/scripts/codex_package/codex-zsh);
the [original build workflow](https://github.com/openai/codex/blob/rust-v0.134.0-alpha.3/.github/workflows/rust-release-zsh.yml)
pins zsh commit `77045ef899e53b9598bebc5a41db93a548a40ca6`.

Rollback is repository-local removal of the new payload/cache directories after
stopping the owned processes. **UNVERIFIED:** destructive rollback has not been
exercised. Existing `.venv`, account state and global configuration remain outside
this build's mutation scope.

## Validation evidence

- **VERIFIED — artifact integrity:** 3 Python archives, 3 Codex archives reconstructed
  from 6 parts, and 69 wheels match their manifest sizes and SHA-256. Codex also
  matches the official pinned npm SHA-512 SRI. No committed artifact exceeds 90 MiB.
- **VERIFIED — unchanged requirements:** 23 packages per platform, matching the
  existing lockfile and dependency manifest. The only wheel script inventory entry
  is XlsxWriter's `vba_extract.py`, a manually invoked XLSM utility; no `.pth` startup
  hooks were found and the utility was not run.
- **VERIFIED — native macOS ARM64:** a fresh extracted Python 3.12.14 installed the
  23 wheels using `--no-index --require-hashes --only-binary=:all:`. All 23 imports
  resolve inside that extracted runtime with `-I`; PDF create/read/PNG raster,
  PPTX create/reopen and XLSX create/reopen passed. Existing `.venv` was not modified.
- **VERIFIED — native bundled Codex on macOS ARM64:** `--version` returned 0.153.4;
  Apple code-signature validation passed. App-server preflight through the new
  transport returned subscription authentication, ready=true and 7 models using
  the original CLI's existing native login. No model turn, credential-file read,
  login mutation or automatic API fallback was performed in this probe.
- **VERIFIED — builder regression:** 7 focused offline tests pass. Source support,
  repeated upstream directory handling and a new invalid download leaving a
  poisoned cache each have retained RED-to-GREEN evidence. Downloads are validated
  before canonical cache publication. An independent reader found no P0/P1 in
  these reviewed builder boundaries; its reproducible cache-recovery P2 was fixed.
- **UNVERIFIED here:** native Windows and Intel macOS execution, a clean-host
  one-click launcher, the complete renderer pipeline and destructive rollback.
  A macOS result does not establish those claims. The integration owner runs the
  separate launch/render/CI checks.

Private local evidence is retained under `.runtime/portable-build/`:
`artifact-integrity.json`, `darwin-arm64-host-probe.json`,
`codex-darwin-arm64-preflight.json`, platform pip-download logs,
`builder-tests-red.log`, `builder-source-directories-red.log`, `builder-download-red.log` and
`builder-tests-green.log`. These scratch records are not copied into the public
runtime bundle. The manifest is the public source/hash/member inventory.
Interruption during archive-part publication has not been exercised. Changed or
incomplete existing files fail verification and require deliberate maintainer
cleanup; the builder does not silently replace them.
