# Intent-Slide

Read `AGENTS.md` for this repository's architecture, commands and invariant rules.
The public product uses each user's unmodified official AI CLI and native login.
Do not inspect or copy authentication files.

Presentation workflow ownership is in `.claude/skills/`. For an actual authoring
request, read the routing dispatcher first and then the selected owner. A worker
executing one stage must not advance to another stage or approve its own outputs.
