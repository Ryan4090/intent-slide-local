# Claude provider evidence and integration boundary

Status: **PARTIALLY_VERIFIED** (2026-09-06). This is a local beta adapter for an end user's unmodified Claude Code CLI and own native subscription. No SDK runtime dependency or credential collection is introduced.

## Verified

- Synthetic subprocess fixtures exercise initialization, public auth metadata sanitization, subscription-only fail-closed checks, request ID `0`, one-time permissions, questions, native capability checks, resume identity, cancellation, queue/stream bounds inherited from the Codex transport, and image-result correlation. These are fixtures, not live model executions.
- An independently reviewed native **Claude Code 2.1.263** executable returned its version and advertised the flags used by the adapter.
- Its real `auth status` returned no login. The provider returned `ready=false`, `auth_mode=none`, `SUBSCRIPTION_REQUIRED`; no model prompt was sent.
- A real initialize-only native control request returned `success`, `current_permission_mode=default`, and a model catalog with `value`, `resolvedModel`, `supportsEffort`, and `supportedEffortLevels`. Only these public fields were inspected. No account identifiers or raw authentication output were saved.
- A second initialize-only query inspected that public capability shape. Sonnet advertised low/medium/high/xhigh/max. The adapter validates explicit effort against the actual initialize catalog before sending user content.

## Unverified / blocked

- **BLOCKED:** actual Claude model generation, permission round trips, restricted/sandbox file enforcement, native image `Read`, and the full G1–G5 workflow. The current user has no Claude subscription; no purchase, login workaround, or API fallback was attempted.
- **UNVERIFIED:** native subscription auth-status variants beyond the allowlisted `claude.ai` / `firstParty` / pro|max|team|enterprise metadata shape. Unknown values fail closed.
- **UNVERIFIED:** cross-version CLI behavior and provider rollback. The required `--restricted` flag establishes minimum version 2.1.248; live framing was probed only on 2.1.263.
- `features` describe implemented adapter operations. `runtime.integration_status=UNVERIFIED` remains visible even when a user's native subscription preflight succeeds. Documented model aliases are not a claim of account entitlement.

## Contract and source decisions

- [CLI reference](https://code.claude.com/docs/en/cli-reference): native auth status, print/stream flags, restricted file tools, explicit tool list, model/effort/session arguments.
- [Headless mode](https://code.claude.com/docs/en/headless): stream-json events and native authentication. `--bare` skips OAuth/keychain and is deliberately omitted.
- [Official SDK control protocol implementation](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/query.py): initialize, can_use_tool, control responses, interrupt. The adapter implements this wire with the standard library; it does not install or modify that SDK or CLI.
- [Sandboxing](https://code.claude.com/docs/en/sandboxing): required native sandbox, failIfUnavailable, no unsandboxed retry, and one-time permission prompts. No `--add-dir` or persistent permission updates are issued.
- [Model configuration](https://code.claude.com/docs/en/model-config): Fable can charge usage credits without asking in print/SDK mode. Fable/best/opusplan and arbitrary model IDs are excluded from this MVP; Sonnet/Opus/Haiku are explicit documented selection profiles. Native usage limits and any user-configured extra usage remain controlled by Claude Code, not this product.
- [Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance): users log in through their own unmodified native CLI. This application does not collect, relay, or inspect OAuth tokens.

The Runner owns approved input snapshots. Claude file tools are restricted to the attempt; owner reference documents must be copied there by the Runner. Native permission restrictions are not bypassed by switching to another tool.
