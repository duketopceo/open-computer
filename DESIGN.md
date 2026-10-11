# open-computer design direction: "Flight Strip"

Status: proposal, 2026-10-10 (Flight Strip tokens). The owner signs off direction and logo.
**Shipped UI:** `services/opsview/` — a passkey-gated local ops page (see [Shipped opsview](#shipped-opsview-2026-10))
uses its own dark tokens (blue accent), not the amber Flight Strip palette below. New surfaces should
either migrate toward Flight Strip or document a deliberate divergence.

## Method and reference lock

Refero MCP tools were not available in this session, so research used the bundled craft
references of the `refero-design` skill plus local product evidence (below). Reference
locks and the decision ledger follow the method anyway.

Evidence used:
- Operator tools whose job is to show state and cost: Linear-style dense lists and the air-traffic
  "flight strip" convention (one row per flight, fixed columns, status colour at the left edge).
- Sibling repos' systems, to stay in the family but not copy it: `orchestral/DESIGN.md` ("The Score",
  paper/ink, Observatory SPA) and `Argus/assets/brand`.
- The product facts that matter: per-subtask cost, deny-by-default egress, human approval gate,
  every state explicit (PR #1 UX bar: keyboard-complete, state-explicit, density over decoration).

**Primary direction:** Flight Strip: the product is a traffic controller for tasks, so every
surface is a ruled strip list with a status edge and a right-aligned number.
**Preserve:** (1) monospace-led data, (2) one accent used only for state and the live path, (3) strips
with a 6 px left edge, (4) numbers right-aligned and tabular, (5) no gradients, no shadows.
**Borrow only:** from orchestral, the paper/ink light theme pairing (not its serif or lane charts).
**Role rules:** accent = "this is live or needs you"; never decoration, never a button fill except the one primary action.
**Reject:** indigo/violet SaaS gradients, glassmorphism, cream/terracotta "calm editorial", decorative serif word swaps, robot or brain imagery, chat-bubble UI.

## Tokens

Contrast measured with the WCAG relative-luminance formula; text pairs need 4.5:1.

| Role | Light | on bg | Dark | on bg |
|---|---|---|---|---|
| bg | `#EDEFF0` | n/a | `#101316` | n/a |
| surface | `#FFFFFF` | n/a | `#181C20` | n/a |
| ink | `#14181B` | 15.5 | `#E9ECEE` | 15.7 |
| muted | `#4A545B` | 6.7 | `#9AA5AD` | 7.4 |
| accent (live / needs you) | `#B4400A` | 4.95 | `#FFB020` | 10.2 |
| ok | `#1B6B3A` | 5.7 | `#4CC27A` | 8.3 |
| warn | `#8A5A00` | 5.1 | `#E0A100` | 8.2 |
| deny | `#B3261E` | 5.7 | `#FF6B61` | 6.7 |
| info | `#0F5C8A` | 6.2 | `#5FB4E8` | 8.2 |

Also: white text on light accent button 5.7:1; dark bg ink on dark accent button 10.2:1.
State is never colour alone: every state also has a glyph and a word (see below).

## Type

- **Data and UI:** a monospace stack (`JetBrains Mono`, `Noto Sans Mono`, `DejaVu Sans Mono`, `monospace`);
  tabular figures; 13/20 in TUI-like panels, 14/22 in docs tables.
- **Prose (README, docs site):** the system sans stack, 16/26, `text-wrap: pretty`.
- **Display (logo only):** the hand-drawn monoline wordmark; no webfont needed.
- Scale: 12 / 13 / 14 / 16 / 20 / 28. Headings are sans, 600, sentence case.

## Glyphs and icons

Square-cap, 1.5 px stroke on a 16 grid, boxed: queued (dashed box), leased (dash), running (triangle),
blocked/awaiting approval (pause bars), denied (cross), done (tick). See `assets/brand/glyphs.svg`.
No stock icon packs. Every glyph is paired with a word.

## Motion

- Only state changes animate: a 120 ms fade for a status edge changing colour, 160 ms slide for a new strip.
- Running strips show a 1 px accent line moving left to right once per 1.6 s. Respect `prefers-reduced-motion` (replace with static accent).
- No loading spinners with indeterminate length: show the step and elapsed time instead.
- No motion in the approval prompt: nothing moves while a human is deciding.

## Surfaces

| Surface | Design |
|---|---|
| **CLI** (`open-computer run`) | One strip per subtask: `state glyph, id, model, sandbox, tokens, $`. Last line: total vs cap. ANSI colours map to tokens; works with `NO_COLOR`. |
| **Approval console (TUI)** | The first real human surface. Shows the exact payload bound for the cloud model, diff vs redacted; keys `a` approve, `d` deny, `i` inspect, `?` help. Command palette on `:`. Every state renders explicitly (queued, leased, running, blocked, denied, tombstoned). |
| **README / docs** | Logo lockup top, strip-style tables, one Mermaid or SVG diagram, dark-mode logo via `<picture>`. |
| **Social card** | `assets/brand/social-card.svg`. |
| **Ops web view (shipped v0)** | Single scrollable page: matrix, route tester, sandboxes, gate audit tail — see below. |

## Shipped opsview (2026-10)

`python -m services.opsview.server` serves a **stdlib + inline CSS** page at `http://127.0.0.1:8791`
(loopback only). Auth is **WebAuthn/passkey** (`fido2`): public `/login`, token-gated `/enroll?token=…`,
then session cookie `oc_sess` (12 h). Unauthenticated GETs redirect to `/login`.

**Authenticated views (one HTML page, section headers):**

| Section | Behavior |
|---|---|
| Control plane | Links `CUBE_API_URL`; UP/DOWN dot from `/health` |
| Determinism matrix | Last run cached in memory; **Run matrix** or **`r`** POSTs `/run` (subprocess: `uv run … pytest tests/conformance -q`) |
| Route tester | Query `/route?q=…` → `services.router.default_router` (engine, tier, cost, ms) |
| Sandboxes | Lists `/sandboxes` when API healthy |
| Gate audit (tail) | Last 12 NDJSON rows from `OC_AUDIT` (default `/tmp/oc-audit/audit.ndjson`) |

**Navigation:** no left-rail shell yet — that is specified in [docs/design/2026-10-11-frontend-design.md](./docs/design/2026-10-11-frontend-design.md) (F1+). Keyboard: **`r`** runs matrix when focus is not in the route input; Enter routes an utterance.

**Shipped tokens (opsview, not Flight Strip):**

| Role | Value |
|---|---|
| Background | `#0b0e11` |
| Foreground | `#d7dde3` |
| Muted | `#7d8a96` |
| Accent (links, hover, focus) | `#4d9fff` |
| OK / fail / pending | `#3fb96f` / `#e05d5d` / `#c9a227` |
| Line / button bg | `#1d242b` / `#141a20` |

Type: system monospace stack at 13 px. States render as words (`UP`, `DOWN`, `PASS`, `FAIL`, `PENDING`).

**Enrollment:** `python -m services.opsview.auth mint <rp-id>` prints a one-time token (10 min TTL);
open `/enroll?token=…` on the same host/origin so `rp_for(host)` matches.

## Decision ledger

| Decision | Source | Role rule | Why |
|---|---|---|---|
| Strip list as core pattern | air-traffic flight strips; PR #1 UX bar | one row per subtask | state-explicit, dense |
| Mono-led type | operator tools; orchestral (data in mono) | numbers and ids only mono in prose | tabular cost columns |
| Single amber/orange accent | product need: "needs you" must stand out | state only | avoids rainbow dashboards |
| Dark amber `#FFB020`, light burnt `#B4400A` | contrast measurement | accent text on bg | both pass 4.5:1 |
| Open-square mark | product concept: one permitted entry | brand only | legible at 16 px |
| No shadows or gradients | reject list | n/a | keeps the terminal and web consistent |
| Glyph plus word for state | accessibility rule | n/a | colour is never the only signal |
