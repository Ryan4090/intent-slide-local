# Intent-Slide repository guide

Intent-Slide is a Windows x64 and Mac arm64/x64 local application. Start with `README.md` and
`docs/local-mvp/01_PORTABLE_CONNECTION_WORK_PACKET.md` for the product scope and evidence boundaries.

## Architecture and commands

- `intent-slide`, `intent-slide.cmd`, `scripts/portable_bootstrap.py`: offline bundled runtime preparation.
- `Start Intent-Slide.command` / `.cmd`: open the authenticated local page.
- `scripts/local_mvp.py`: local setup, doctor and start.
- `vendor/portable/`: reviewed Git-contained archives, wheels, source and license manifests.
- `projects/presentation-agent-suite/src/presentation_agents/v2/`: contracts,
  SQLite store, engine, provider adapters, queue, HTTP service and validation.
- `projects/presentation-agent-suite/console/`: dependency-free browser UI.
- `.claude/skills/`: canonical presentation workflow owners and converter tools.
- `.codex/skills/`: generated discovery pointers; update only with the canonical
  `sync_codex_stubs.py` script.
- `scripts/build_local_release.py`: allowlisted, committed-tree-only release ZIP.
- `.runtime/` and the suite's `.runtime/`: private local state, never release inputs.

Use `.runtime/portable/<platform>/environment/bin/python` (Windows: `Scripts/python.exe`) after the OS launcher prepares it. An existing development `.venv/bin/python` may run unit checks; it is not a distribution prerequisite. Useful maintainer checks:

```sh
./intent-slide doctor --provider codex --connect
PYTHONPATH=projects/presentation-agent-suite/src .venv/bin/python -m unittest discover -s projects/presentation-agent-suite/tests -p 'test_v2*.py'
node --test projects/presentation-agent-suite/console/tests/*.test.mjs
.venv/bin/python -m unittest discover -s tests -p 'test_local*.py'
```

## Invariants

Keep intent, research and design phases distinct. Each phase adopts only its own
validated outputs. G1/G2/G3 approvals belong to the user and are bound to current
artifact hashes. G4 checks the actual PPTX; G5 requires actual current-image tool
observations. Fixture events and reported intentions never substitute for those
observations. Progress comes from valid acceptance units, not elapsed time.

Queue provider/model/effort as an immutable selection. Resume only a compatible
provider session. Keep native CLI authentication intact; do not read, store or
relay credentials. Preserve loopback, session, CSRF, sandbox and per-job approval
boundaries. Never turn a denied action into an alternate-tool workaround.

For actual slide authoring, read `.claude/skills/ppt-master/workflows/routing.md`
first, then the selected owner. SVG page authorship stays sequential within one
design worker. Intent and research workers do not perform downstream authoring.

Changes to shared contracts require relevant regression evidence and independent
review. Render material UI changes. Keep actual CLI/renderer/browser verification
separate from fixture tests. Claude integration remains beta until real subscribed
model execution and image/permission behavior are verified.

Preserve existing user projects and state. Publish only the release allowlist;
never include private prototype history, raw conversations, credentials or runtime
files. Reviewed vendor/portable payloads are product inputs, distinct from private .runtime state. Retain upstream and dependency license notices. Never add runtime downloads or Git LFS/submodule prerequisites to the clone-only launch path.
