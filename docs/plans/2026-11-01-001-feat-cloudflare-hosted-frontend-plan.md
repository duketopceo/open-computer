---
title: Cloudflare-hosted frontend - Plan
type: feat
date: 2026-11-01
artifact_contract: ce-unified-plan/v1
execution: code
status: awaiting owner OK
---

# Cloudflare-hosted frontend - Plan

## Goal Capsule

- **Objective:** serve the React UI in `ui/` from a Cloudflare Worker (static assets) so the console shell loads when the owner's devices are off. The backend (opsview, port 8791) stays on the owner's devices behind the existing Cloudflare Tunnel.
- **Not in scope:** moving any backend work to Cloudflare; changing how runs, devices or sandboxes execute; a second UI.
- **Stop conditions:** no live Cloudflare account call, deploy or secret in any test; this PR is the plan only, no code.
- **Hostnames are placeholders:** `console.example.com` (public, Worker) and `origin.console.example.com` (tunnel, private).

## What the code does today (verified)

- opsview serves `ui/dist` itself (`g_spa`, `g_spa_route`, `g_asset` in `services/opsview/server.py`) behind a passkey session (`oc_sess`, `HttpOnly; SameSite=Strict; Secure` off loopback).
- Every POST checks `Origin` against the request `Host` and an `X-CSRF-Token` from `GET /api/session`. A proxy that changes `Host` without fixing `Origin` gets 403.
- The passkey relying-party id comes from the `Host` header (`rp_for`); `OC_RP_PARENT=example.com` makes all subdomains share one rp id.
- The SPA only calls same-origin paths: `/api/*`, `POST /goals`, `/_frag/*`. `ui/src/lib/api.ts` already maps a network error to an `offline` link state and a 401 to `auth`.
- SSE is `GET /goals/<id>/events` with `Last-Event-ID` resume (`docs/design/event-stream.md`).
- Server-rendered routes still live in opsview: `/login`, `/enroll`, `/webauthn/*`, `/workspace*`, `/runs/new`, `/runs/<id>`, `/goals/new`.

## Recommendation: one Worker, same origin, proxy everything dynamic

One Worker on `console.example.com` serves `ui/dist` as static assets and forwards every other path to `origin.console.example.com` (the tunnel). Static paths (`/`, `/assets/*`, SPA deep links such as `/devices`) never touch the backend.

| | A. Same-origin proxy (recommended) | B. Separate `api.` subdomain + CORS |
|---|---|---|
| Cookies | `oc_sess` stays host-only and `SameSite=Strict`; Access cookie is first-party | cross-site cookies need `SameSite=None` and a `Domain`, weakening the CSRF posture |
| Passkeys | rp id unchanged with `OC_RP_PARENT` | same, but `/login` lives on a different host than the app |
| CORS | none | new allowlist, credentialed preflights, CSRF header handling in opsview |
| SSE | streamed through the Worker | direct to tunnel, but needs CORS on `EventSource` (no custom headers) |
| Server-rendered pages | work unchanged (proxied) | two hostnames, redirects cross origins |
| Cost | every API call is a Worker request (free plan: 100k/day) | API calls skip the Worker |
| Backend change | small (see KTD2) | larger (CORS, cookie attributes) |

A is chosen because it keeps the current cookie, CSRF and passkey model intact. B's only advantage is Worker request count, which is an open question below, not a design driver.

## Key Technical Decisions

- **KTD1 Routing split.** `wrangler.jsonc` uses Workers Static Assets with `not_found_handling: "single-page-application"` and `run_worker_first` set to the dynamic prefixes (`/api/*`, `/goals*`, `/_frag/*`, `/login`, `/enroll`, `/webauthn/*`, `/workspace*`, `/runs/new`, `/runs/run_*`, `/static/*`). Everything else is served from assets with no Worker invocation. The dynamic list is a single constant shared by the Worker and its tests, checked against opsview's `GET_ROUTES`/`POST_ROUTES` (U3).
- **KTD2 Host and Origin.** The Worker forwards to the tunnel host, so opsview sees a different `Host` than the browser's `Origin`. Decision: the Worker rewrites `Origin` and `Referer` to the tunnel host on forwarded requests (no backend change), and `OC_RP_PARENT` is set to the shared parent domain so the rp id is the same on both hosts. Rejected: a trusted `X-Forwarded-Host` mode in opsview (more attack surface for a gate that currently trusts only `Host`).
- **KTD3 Origin is private.** The tunnel hostname sits behind its own Access application that accepts only a service token; the Worker sends it. A direct hit on the origin hostname without the token never reaches opsview. The passkey gate stays on as the second factor.
- **KTD4 Header hygiene.** The Worker strips `CF_Authorization` and the Access service headers from responses, forwards `Cookie` (`oc_sess`) and `Set-Cookie` untouched, passes `Accept` and `Last-Event-ID`, and sets no caching on proxied responses (`Cache-Control` from opsview wins; `no-store` on `/api/*`).
- **KTD5 SSE.** The Worker returns the upstream `Response` body unbuffered (no `await text()`), forwards the abort signal, and keeps no state. Resume is the existing `Last-Event-ID`; the 15 s `: hb` comment keeps the stream from idling out.
- **KTD6 Security headers.** opsview's CSP (`default-src 'none'; script-src 'self'; connect-src 'self'; ...`) moves to a `ui/public/_headers` file so the statically served shell carries the same policy. The `ui/dist` build must stay free of inline scripts (already true; U1 checks it).

## Offline state

- **Probe.** The SPA polls `GET /api/session` (already called on load) and the Worker adds `GET /_edge/health`, which returns `{edge:"ok", origin:"up"|"down"}` after a 2 s timed fetch of the tunnel. No auth data in the body.
- **Screen.** When `origin` is `down` (or the proxy returns 502/504/timeout), the UI shows "Your computer is offline" with the last-seen time and a retry, instead of a blank page. It reuses the existing `link: "offline"` state in `ui/src/App.tsx` and `ApiError` kind `network`.
- **Keep last data.** The last successful payloads of the read endpoints used by Overview, Runs, Devices and Approvals are kept in memory and `sessionStorage` (no secrets, no CSRF token), shown dimmed with a "last updated" label. Writes are disabled while offline, never queued.
- **Server-rendered routes offline.** The Worker returns a small static "offline" HTML page (same tokens) with status 503.

## Server-rendered routes

All of `/login`, `/enroll`, `/webauthn/*`, `/workspace*`, `/runs/new`, `/runs/<id>`, `/goals/new` are proxied through as-is, including redirects (Worker uses `redirect: "manual"` and rewrites `Location` from the tunnel host to the public host). Nothing is re-implemented at the edge. Moving the run pages into the SPA is already tracked as OC-089 and is not a prerequisite.

## Deploy

`.github/workflows/deploy-console.yml`: on push to `main` touching `ui/**` or `edge/**`, build with `pnpm --frozen-lockfile`, then `wrangler deploy`. Pull requests run build and Worker tests only, with no secrets (`pull_request`, never `pull_request_target`). Actions pinned by SHA like `ci.yml`; `permissions: contents: read`; a `production` environment so deploys can require a manual approval.

Secrets (names only):

| Where | Name | Purpose |
|---|---|---|
| GitHub environment `production` | `CLOUDFLARE_API_TOKEN` | Workers deploy, scoped to one account and the Workers Scripts edit permission |
| GitHub environment `production` | `CLOUDFLARE_ACCOUNT_ID` | target account |
| Worker secret | `OC_ORIGIN_ACCESS_CLIENT_ID` | Access service token id for the tunnel host |
| Worker secret | `OC_ORIGIN_ACCESS_CLIENT_SECRET` | Access service token secret |
| Worker var (not secret) | `OC_ORIGIN_HOST` | tunnel hostname, e.g. `origin.console.example.com` |
| Device, existing env | `OC_RP_PARENT` | shared parent domain for the passkey rp id |

## Access policy

- **Public host (Worker):** Access application on `console.example.com`, allow only the owner's identity (email or IdP group), short session, no bypass rules. The `/_edge/health` path is the only exception and returns no data beyond up/down.
- **Origin host (tunnel):** Access application allowing only a service-token policy. No human policy.
- **Tallyline (second consumer, separate plan in its repo):** same shape, with its own two hostnames and tokens.

## Implementation Units

| Unit | Work | Verification gate |
|---|---|---|
| U1 Static assets | `edge/` workspace: `wrangler.jsonc`, `ui/public/_headers`; `ui` builds with no inline script | `pnpm build` green; test fails if `dist/index.html` has an inline script; `wrangler deploy --dry-run` in CI |
| U2 Proxy Worker | `edge/src/index.ts`: route split, Origin/Referer/Location rewrite, service-token headers, header hygiene | vitest (`@cloudflare/vitest-pool-workers`) with a fake origin: POST with CSRF passes; Origin is rewritten; `CF_Authorization` not forwarded; redirect `Location` rewritten; each case fails against a broken Worker |
| U3 Route parity | shared dynamic-prefix list plus a pytest that imports opsview `GET_ROUTES`/`POST_ROUTES` and asserts every opsview path is either in the list or a static SPA path | pytest red when a new opsview route is added without updating the list |
| U4 SSE | streamed passthrough, abort and resume | vitest: chunks arrive before the origin finishes; `Last-Event-ID` forwarded; client abort cancels upstream |
| U5 Offline UX | `/_edge/health`, offline screen, last-data cache, static 503 page | Playwright (existing `tests/e2e`) with origin killed: offline screen shown, last data dimmed, write controls disabled, axe clean |
| U6 Deploy | workflow, environment, docs/runbooks/cloudflare-frontend.md (Access setup steps) | workflow lint (`actionlint`); dry-run on PR; first real deploy manual with the owner watching |
| U7 End-to-end check | owner-run: sign in with passkey through the public host, start a goal, watch SSE, stop the tunnel, see offline screen | recorded in the PR; no unit counts as done without it |

## Risks

- **Passkey rp id changes** if `OC_RP_PARENT` was unset at enrolment: existing passkeys stop matching. Check first; re-enrol if needed.
- **Free-plan request limit** (100k Worker requests/day) with polling UIs plus SSE reconnects; measure in U7, move to the paid plan (about $5/month) if close.
- **Worker bug becomes an auth bug:** it forwards cookies and rewrites `Origin`. U2 tests are the control; the origin Access token plus the passkey gate remain the backstops.

## Open questions for the owner

1. **Hostnames:** which public host and which tunnel host? Is the tunnel hostname already public, so it needs a new Access app?
2. **`OC_RP_PARENT`:** is it set today, and to what? If not, are you fine re-enrolling passkeys?
3. **Identity:** which email or IdP should the Access policy allow?
4. **Plan tier:** is the free Worker plan acceptable, or start on paid?
5. **Offline cache:** is keeping last data in `sessionStorage` acceptable, or memory only (lost on reload)?
6. **Server-rendered pages:** keep proxying them indefinitely, or prioritize moving `/login` and `/workspace` into the SPA?
