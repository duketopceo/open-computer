# open-computer design direction

Two directions live here. **Console (people)** is what the web console ships for everyone. **Flight Strip** is for the
CLI, the approval TUI and the console's Developer tools. Plan: `docs/plans/2026-10-31-002-feat-general-market-ux-plan.md`.

## Console (people), 2026-10-31

The web console is for people who are not technical. It follows the shape of Perplexity Computer: one task box with an
effort picker, a short left nav, and plain words. This section supersedes Flight Strip for the console's default
views. Flight Strip still governs the rules marked "carries over".

- **Thesis:** a calm paper canvas, one teal accent for "this is yours to act on", a serif for the single question on the page,
  plain sans for everything else. A notebook with one pen on it, not a cockpit.
- **Tokens:** `ui/tailwind.config.ts` is the source (canvas `#faf9f6`, ink `#1a1a18`, muted `#6b6b64`, line `#e7e4dc`, teal `#0f766e`).
  Soft card shadow is allowed; gradients are not.
- **Navigation:** at most five primary entries (New, Tasks, Needs you, Automations, Recent). Operator and developer screens sit behind
  the **Developer tools** switch. Under 768 px the sidebar is a slide-in menu.
- **Cards** only where the card is the interaction (a task, an approval, a starter idea). Lists are stacked rows, not sideways tables.
- **Words:** sentence case, verbs on buttons, no ids, model ids, paths or env var names outside Developer tools. The glossary in the
  plan is the source of truth (`ui/src/lib/words.ts`).
- **Carries over from Flight Strip:** state is a glyph plus a word, never colour alone; the accent means "live or needs you";
  text pairs meet 4.5:1; every state is explicit; reduced motion is respected; no chat-bubble thread UI.
- **Dark theme:** not yet implemented in the React console; when added it reuses the Flight Strip dark tokens below.

---

# Flight Strip (CLI, approval TUI, Developer tools)

Status: proposal, 2026-10-10. The owner signs off direction and logo. The product has no UI yet;
this fixes the look of the surfaces it will have.

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
| **Later: web ops view** | Same strip list in a browser, only if the TUI proves insufficient; own design pass first. |

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
