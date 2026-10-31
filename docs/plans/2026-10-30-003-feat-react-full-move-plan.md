# Plan: Full move to the React shell — retire Python-rendered pages

## Problem

The product serves two UIs. The React shell (`ui/dist`, dark token set)
covers `/`, `/devices`, `/sandboxes/:id`, `/router`, `/audit` (PR #85).
Every other surface still renders from `services/opsview/views.py`
(silver-lining shell, different chrome), so a user clicking Goals,
Runs, Approvals, Overview, Automations, or Skills drops into "the wrong
app". Goal: every navigable path renders inside the React shell;
`views.py` serves only the API layer + auth + workspace renderer, then
shrinks to those.

## Current route map (verified)

| Path | Renderer today |
|---|---|
| `/` | React SPA (`g_spa`) |
| `/devices`, `/sandboxes`, `/sandboxes/:id`, `/router`, `/audit` | React SPA (`g_spa_route`) |
| `/overview` | views.py `page_overview` |
| `/goals`, `/goals/new`, `/goals/<id>` | views.py + goalruns.py |
| `/runs`, `/runs/new` | views.py |
| `/approvals` | views.py `page_approvals` — the human gate |
| `/automations` | views.py |
| `/skills` | views.py |
| `/gate`, `/matrix`, `/ops`, `/fleet`, `/enroll`, `/route`, `/run` | views.py misc |
| `/workspace` | `services/workspace/` own renderer — stays (own product surface) |
| `/login` | auth — stays server-rendered |

## Port order (one PR per slice)

1. **Approvals** — highest daily value and the risk surface. Needs
   `GET /api/approvals` (queue rows: id, kind, payload hash, age, goal,
   reason) and `POST /api/approvals/<id>/{approve,deny}` — reusing the
   exact existing `gate` decision path (`gate/queue.py`), never
   reimplementing it. Requirements that must survive the port:
   payload-hash binding shown to the human, single-use decision,
   CSRF, `needs_confirm` for irreversible rows, deny path identical.
   SPA e2e: approve flow end-to-end against a fixture queue.
2. **Runs** — `GET /api/runs` exists; add `GET /api/runs/:id` summary
   rows (worker_failures already friendlified). Port list + summary
   panel. `/runs/new` matrix runner stays server-rendered until a
   later slice (it's an ops tool, not the daily surface).
3. **Goals** — `GET /api/goals` list + `GET /api/goals/:id`. The goal
   detail already exists in `goalruns.one()` as JSON — reuse it.
   `/goals/new` submit form: port as a small composer reusing the
   existing `/goals` POST (confirm flow included).
4. **Overview** — `GET /api/overview` aggregate endpoint (health,
   counts, cost) then port.
5. **Automations + Skills** — read-mostly surfaces; JSON endpoints +
   port.
6. **Misc ops surfaces** (`/gate`, `/matrix`, `/ops`, `/fleet`,
   `/enroll`, `/route`, `/run`) — decision per page: port, or keep as
   linked ops-only surfaces behind an "Admin" section in the new
   sidebar. `/login` stays server-rendered always.
7. **Cutover** — nav in `Sidebar.tsx` already points at all routes;
   each slice flips `server.py`'s route table from `g_…` python page
   to `g_spa_route`; the old `page_*` renderer is deleted in the same
   PR (no dual-serve drift). Final PR deletes `views.py`'s
   silver-lining chrome + `console.css`.

## Constraints

- No behavior changes to the gate, placement, or audit semantics —
  this is presentation + thin JSON surface only.
- Each ported page keeps the POST+CSRF convention already in api.ts.
- Friendly labels apply everywhere (lib/friendly.ts is the single map —
  port its words to any new server-rendered leftovers only as needed).
- SPA e2e (spa.e2e.mjs) covers each new page: fixture data, axe clean,
  no horizontal scroll at 390px.

## Risks

- Approvals gate regression — mitigated by reusing `gate/queue.py`
  verbatim and e2e covering approve+deny.
- Dual-serve drift — mitigated by deleting the python renderer in the
  same PR that flips the route.
- views.py deletion in the final PR touches shared helpers (`status`,
  `empty`, icon sprite) — keep those only if the workspace renderer
  uses them; else delete whole file.
