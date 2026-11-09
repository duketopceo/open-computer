# open-computer design direction: "Flight Strip"

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

---

# Console surface and chat (2026-10-31)

Plan: `docs/plans/2026-10-31-003-feat-chat-agent-product-plan.md`. This section governs the web console (threads, composer,
settings). It supersedes the Flight Strip rejects "no gradients, no shadows" and "no chat-bubble UI" for the console only;
Flight Strip still governs the CLI, the approval TUI and the Developer area in Settings.

- **Look:** a designed light theme and a designed dark theme, picked by `prefers-color-scheme` with a manual override in Settings
  (stored as `oc-theme`; `ui/src/lib/theme.ts` and `theme-init.js` set `data-theme`, `system` removes it).
- **Depth and texture, CSS only:** a base canvas, a grain layer (inline SVG noise, 3-4% opacity), a soft radial glow behind the
  composer (accent at about 6%), a faint vignette, a translucent sidebar over the canvas, two shadow tiers, 1 px hairline borders.
  No image assets. Honour `prefers-reduced-motion` and `prefers-reduced-transparency`.
- **Tokens:** superseded 2026-10-31 by plan KTD11 (`docs/plans/2026-10-31-003-feat-chat-agent-product-plan.md`): the console shares
  Tallyline's Silver Lining neutral tokens from `services/opsview/static/silver-lining.css`, dark and light, system default with a
  toggle. The earlier charcoal and teal starting point is retired. Values (dark / light): canvas `#090909` / `#f5f5f5`, surfaces
  `#0f0f0f` to `#252525` dark, accent Azure `#339fee` / `#005fba` (the web console now overrides neutrals and accent with Iris; see "Colour scheme (2026-11-09)"). `silver-lining.css` is a plain copy of Tallyline's generated
  file from its main branch (`packages/silver-lining/scripts/sync-open-computer.ts`); never hand-edit it. Open Computer's own
  scale tokens (type, space, radius, density, motion) and the `--color-*-soft` aliases live in `oc-scale.css`. The SPA imports
  both through the `@tokens` Vite alias and `ui/tailwind.config.ts` maps its colour names to the `--sl-*` variables.
- **Threads:** the owner's messages are compact; the agent's answer is a document-style block, not a bubble. Steps, tool calls and
  subagent work are collapsed cards that show real model, cost and time. Approvals are cards that show who asks, what leaves,
  where, cost, PII and the payload hash.
- **Navigation:** New thread, Threads (recents with a needs-you count), Automations, Artifacts, Connectors, Settings. Operator
  pages live under Settings > Developer.
- **Carries over:** state is a glyph plus a word, never colour alone; text pairs meet 4.5:1; every state is explicit.
- **Run timeline (2026-11-05, dev mode):** `#goal_x/timeline` shows one card per step from the run event stream: state glyph and
  word, title, a model chip and a quieter "Routed by" chip, elapsed, cost, and a waiting-on line; reasoning, plan, commands and
  output open on demand. Only a running step uses the accent. "Waiting" uses the leased dash and "Skipped" a muted dashed box;
  neither is accent. Above 60 steps the list is virtualized with measured rows. A new card slides in over 160 ms and the running
  line is static under reduced motion. The Dev trace switch under Operator asks before turning on, because it captures content.

## Motion and depth (2026-11-08)

Signed off by Luke on 2026-11-08. This section amends "Depth and texture, CSS only" above for the web console. Flight Strip
still governs the CLI, the approval TUI and Settings > Developer.

- **Allowed:** one subtle ambient shader behind the canvas (never behind text, cards or the sidebar), and the Motion library
  (`motion`, loaded through `LazyMotion` + `m`) for layout and enter/exit motion in the run timeline. Everything else stays CSS:
  View Transitions for thread and page changes, `@property` for the composer edge while a run works.
- **The shader:** loaded on demand in its own chunk with its own byte budget, raw WebGL (no three.js), capped frame rate, paused
  while the tab is hidden or the canvas is off screen. Shader source copied from ThreeUI Community (MIT) keeps its credit in
  `ui/src/vendor/`. Without WebGL, and whenever effects are off, the CSS weave is the background.
- **Timing:** motion tokens come from Rune's spring presets (anomalyco/rune `rune-motion`, MIT): snap 250 ms, settle 300 ms,
  reflow 400 ms, morph 300 ms with a small bounce. They live in `oc-scale.css` as `--motion-*` and in `ui/src/lib/motion.ts`.
- **Never moves:** approval cards and anything a human is deciding on; text a person is reading (no parallax, no drifting copy);
  the accent stays "live or needs you", never decoration.
- **Always respected:** `prefers-reduced-motion: reduce` and Settings "Reduce effects" (`data-effects="off"`) stop every animation,
  the shader and View Transitions; the settled state shows at once and nothing is ever hidden waiting for an animation.
- **Budgets:** the main SPA chunk stays under 130 kB gzip; the shader chunk under 8 kB gzip. CI fails the build past either.
- **Still rejected:** glassmorphism, 3D scenes, particle fields, glow used as decoration, robot or brain imagery.

## Colour scheme (2026-11-09)

Luke asked for a real colour scheme: the console was near-monochrome with a blue Send button that did not belong to anything.
This section amends the palette line in "Look" above for the web console. Status colours, overlays and the high-contrast
themes stay Silver Lining's.

- **Iris:** one accent, iris violet, plus cool slate neutrals. It is distinct from every status colour (red, amber, green,
  grey), so "live or needs you" never reads as "done" or "failed".
- **Tokens** live in `services/opsview/static/oc-palette.css`, loaded after `silver-lining.css`. Never edit the Silver Lining copy.

| Token | Dark | Light |
| --- | --- | --- |
| `--sl-canvas` | `#0a0b10` | `#f6f6f9` |
| `--sl-surface-1` / `-2` / `-3` / `-4` | `#0f1017` / `#15161f` / `#1c1e28` / `#232532` | `#ffffff` / `#eeeef3` / `#e7e7ef` / `#dfe0e9` |
| `--sl-lining` | `#424459` | `#aeb0c0` |
| `--sl-ink` / `-muted` / `-subtle` | `#e6e6ec` / `#c9cad6` / `#a3a4b3` | `#17171f` / `#33343f` / `#52536a` |
| `--sl-accent` (hover, pressed) | `#8f8cf7` (`#a9a6ff`, `#7672e8`) | `#4f46d8` (`#443cc4`, `#3a33ad`) |
| `--sl-on-accent` | `#0a0b10` | `#ffffff` |

- **Use:** the accent marks a live run, something that needs you, focus, and the one primary action (Send, Connect).
  Nothing else gets it.
- **Thinking glow:** while a chat's run works, an accent arc sweeps the composer edge over a soft halo. It is the one
  ambient "working" signal. Reduced motion and Reduce effects keep the ring and halo, static.
- **Checked:** `ui/src/lib/palette.test.ts` holds accent and inks at 4.5:1 or more on canvas, surface-1 and surface-3, and
  on-accent at 4.5:1 or more on the accent, in both themes. Axe runs in e2e.
