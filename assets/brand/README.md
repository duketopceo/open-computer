# open-computer brand assets (PROPOSAL)

Status: proposal. The owner signs off logos; nothing here is final. All files are
hand-written SVG generated from simple geometry (no AI imagery, no stock icons, no raster logos).
PNG files in `preview/` are `resvg` renders optimised with `oxipng`, for review only.

## Mark (`mark.svg`, `mark-dark.svg`), 32 x 32 grid

- An **open square**: a 3-unit stroke square (4,4)-(28,28) whose left edge has a gap from y=12 to y=20.
  The square is the disposable sandbox; the open edge is the only way in.
- A **route line** (accent) enters through the gap from the left: the task, arriving by the one
  permitted path (default-deny egress, explicit allowlist).
- A **dot** (accent, r 3.6) inside: the agent at work.
- Three shapes, two colours, round caps. It holds at 16 px (`preview/mark-16.png`): the gap and
  dot stay distinct because the gap is 8 units wide and the dot is 7.2 units across.

## Wordmark (`wordmark.svg`, `wordmark-dark.svg`)

Lowercase `open-computer`, monoline, drawn as stroked paths (no font dependency): x-height 12,
stroke 2.4, round caps, letter gap 5. Built from only a rounded rectangle, arcs and straight stems.

## Lockup (`logo.svg`, `logo-dark.svg`)

Mark at 32 units plus wordmark scaled 1.5, 12 units gap.

## Other

- `glyphs.svg`, `glyphs-dark.svg`: six subtask-state glyphs on a 16-unit grid, 1.5 stroke, square caps:
  queued (dashed), leased, running, blocked awaiting approval, denied, done. Meant for the TUI/docs.
- `social-card.svg`: 1200 x 630 dark card. Uses the system monospace stack (Noto Sans Mono, DejaVu Sans
  Mono) for text, so wording renders in whichever is installed. Values on it are illustrative.

## Colour

Light: ink `#14181B`, accent `#B4400A`. Dark: ink `#E9ECEE`, accent `#FFB020`. See `DESIGN.md` for contrast ratios.
