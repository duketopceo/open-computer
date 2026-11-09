---
title: Accounts, Composio connectors and chat polish - Plan
type: feat
date: 2026-11-09
artifact_contract: ce-unified-plan/v1
execution: code
depth: standard
related: docs/plans/2026-11-06-1222-feat-everyday-chat-ui-plan.md; ROADMAP.md; DESIGN.md
---

# Accounts, Composio connectors and chat polish - Plan

**Source:** Luke's feedback on the live site and one test chat, 2026-11-09. Verified against `main` at `e4be95a`.

## Goal

- Each person using Open Computer has an account, can see it under Settings, and can connect their own apps through Composio.
- The chat screen is calmer and explains more: a real colour scheme, less jargon on the run card, live progress you can follow, and a side panel for sources and tool calls.
- Every model call goes through OpenRouter. No local models.

## Decisions

- **KTD1. Sign-in stays Cloudflare Access (plus passkeys and loopback).** The console already authenticates every request. Behind Access, `who` is the verified Access email. On loopback it is `local`. A second email-and-password system would duplicate that with a weaker factor. "Basic login" therefore means an **account record** for each signed-in identity, with a stable id, display name and sign-out. If OC is later served without Access, add local accounts then, behind a flag.
- **KTD2. Composio user id = the account id, never the email.** Composio's guidance is a stable id. `oc_` + 16 hex of sha256(lower(identity)), stored in `accounts.json`.
- **KTD3. Composio is inert without `COMPOSIO_API_KEY`.** The key is read from env only, never logged, never sent to the browser. Without it, `/api/connectors` answers `{enabled: false}` and the Connectors section says how to turn it on. The SDK is imported lazily.
- **KTD4. Connect with Composio-managed OAuth through a session:** `composio.create(user_id=...)`, then `session.authorize(toolkit, callback_url=...)`. Composio returns a hosted Connect Link. The callback lands on `/settings?connected=<toolkit>`. OC never sees provider tokens.
- **KTD5. The toolkit list is curated** (`OC_COMPOSIO_TOOLKITS`, default gmail, googlecalendar, googledrive, github, slack, notion). Connect and disconnect refuse anything outside it, and refuse an account id the user does not own.
- **KTD6. Tools reach the agent in a follow-up PR (U4).** A step worker with an active Composio session gets `session.tools()` in its OpenRouter call, and each call is executed through `session.execute`. It ships with its own tests and an approval rule for write tools. Accounts and connections land first so Luke can connect apps today.
- **KTD7. Settings stays one page of sections.** Account and Connectors are their own components (`ui/src/pages/settings/`), each rendered with one line in `Settings.tsx`, so parallel work (runtime map, Scrapling, Kurultai link) adds sections without conflicts. A tab shell can come later, once those land.

## Units

| U | What | Tests | PR |
|---|---|---|---|
| U1 | Stronger ambient background; effort caption cut to one line, details in Advanced | `EffortPicker.test.tsx`; e2e effort flow | #144 |
| U2 | Routing: no local placements, every model call via OpenRouter with fallbacks; paused runs hold steps at dispatch; PII email pattern needs a TLD; approving a held plan resumes the chat | placement table I0 over 1,152 cases; health; conformance; cost-ledger pause; threads resume | #148 |
| U3 | Accounts (`services/accounts`), `GET/POST /api/account`, sign-out; Composio connectors (`services/connectors/composio.py`), `GET /api/connectors`, connect and disconnect; Settings Account and Connectors sections | pytest: account ids, store, API; Composio wiring with the SDK mocked (off without key, connect, ownership check, key never in responses or logs); vitest for both sections | this plan's PR |
| U4 | Composio tools in step workers, gated: read tools run, write tools ask first | pytest with the session mocked | next |
| U5 | Colour scheme: one accent plus neutrals as `--sl-*` tokens in DESIGN.md; Send uses the accent; keep and strengthen the composer thinking glow | vitest token test; axe contrast in e2e | next |
| U6 | Run card copy: drop `LEASED`, `origin cloud`, `routed by jev` and `<$0.0001` from the card; plain state words; details move to the click-in view | vitest for `RunView` compact; e2e text checks | next |
| U7 | Live progress: a one-line, plain-words "now doing…" line on the card, fed by the run's event stream; click opens the live view | vitest with a fake stream | next |
| U8 | Right side panel (collapsible): sources, web searches and tool calls for the current run, from the event stream | vitest; e2e open/close and keyboard | next |

## Verification

- `uv run --locked ruff check .` and `uv run --locked pytest -m "not live"` pass. `pnpm test` and `pnpm build` pass within budget. Console e2e passes in CI.
- After deploy: `http://localhost:8791` returns 200 on the host, and the public URL returns 302 to Cloudflare Access.
- With `COMPOSIO_API_KEY` unset, Settings shows Connectors as off and no request goes to Composio.
