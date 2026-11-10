# Public demo: what it is and how to put it behind Cloudflare Access

Status: documented, **not deployed**. Nothing in this repo creates a Cloudflare resource, DNS record or deploy. Every step
below is run by the owner, by hand.

## What the demo is

A build of the console (`pnpm --dir ui build:demo`) in which every API call is answered from in-bundle fixtures. It is
**fixtures, not a capped sandbox**: no backend, no tunnel, no model call, no key, no compute.

- Sample data only: three chats, three runs (done, waiting for approval, failed), approvals, devices, sandboxes, audit rows.
- Writes are simulated in memory: a sent message gets one canned reply, an approval flips state, "Connect" does nothing real.
  A reload resets everything. A banner says so on every page and cannot be dismissed.
- The server-rendered pages (`/work`, `/workspace`) are not offered; dictation, Dev trace, downloads and OAuth are off.
- It is built with a compile-time flag. The normal build contains no demo code and refuses to run with `VITE_DEMO` set
  (`ui/scripts/check-demo-bundle.mjs` and `ui/scripts/edge-config.test.mjs` prove both).

Why fixtures: a visitor never touches the owner's hardware, keys or spend, by construction. A capped hosted sandbox needs
per-visitor isolation, quotas and abuse handling that do not exist yet (open question 1 in the plan).

## Build and check locally

```bash
cd ui
pnpm install --frozen-lockfile
pnpm build:demo          # tsc, vite build --mode demo, byte budget
pnpm check:demo          # demo bundle has the fixtures, normal bundle does not; no inline script; referenced files exist
cd ../tests/e2e && npm run test:demo   # zero /api and zero off-site requests on every route, axe clean
```

`pnpm dev:demo` runs the same thing with hot reload.

## Deploy shape

`edge/wrangler.jsonc` is Workers Static Assets only: `assets.directory` is `../ui/dist-demo`, SPA fallback on, **no `main`**
(no Worker code), no bindings, no vars, no secrets, `workers_dev: false` and `preview_urls: false`. `ui/public/_headers`
carries the CSP (`default-src 'none'; script-src 'self'; connect-src 'self'` and so on), `nosniff`, no referrer, no framing.

Hostnames below are placeholders: `demo.example.com`.

## Owner steps (not executed by this repo)

1. **Build:** `pnpm --dir ui build:demo` and `pnpm --dir ui check:demo`.
2. **Deploy the static assets, once, by hand:** `cd edge && npx wrangler deploy` (log in with your own account; the Worker name
   in `wrangler.jsonc` is a placeholder). No token is stored in the repo or in CI.
3. **Attach a custom domain** to the Worker (Workers & Pages > the Worker > Settings > Domains), for example
   `demo.example.com`. `workers.dev` and preview URLs are disabled in the config, so this is the only entry.
4. **Put Access in front of it** (Zero Trust > Access > Applications > Add > Self-hosted):
   - Application domain: `demo.example.com`, path empty (covers every route).
   - Session duration: 24 hours.
   - Identity providers: enable **GitHub** and/or **Google** for sign-up (anyone with an account can sign in, which is
     "sign-up" in Access terms), and/or **One-time PIN** for an invited list.
   - Policy, open demo: Action Allow, Include `Everyone` (with at least one IdP above; one-time PIN alone would still need an
     email rule). Policy, invite-only: Include `Emails` with the allowed addresses.
   - No Bypass rule. Do not add `/*` exceptions.
5. **Verify** in a private window: the demo hostname redirects to the Access login; after sign-in the banner shows; the
   `*.workers.dev` URL returns 404; `curl -I https://demo.example.com` without a session returns the Access redirect, not the app.
6. **Rollback:** delete the Access application (the demo becomes unreachable only if you also remove the route), then delete the
   Worker route or the Worker. Nothing else holds state.

This is separate from the owner-only live console in `docs/plans/2026-11-01-001-feat-cloudflare-hosted-frontend-plan.md`
(OC-092), which proxies to the owner's own machine behind the passkey and a service token. Do not point the demo hostname at that.

## Open question (owner): the production backend

The demo is fixtures only. Which path serves real users later is undecided:

| Option | What | When |
|---|---|---|
| A. Fixtures (this demo) | No backend | Now; safe to leave public |
| B. Worker + Tunnel to the owner's machine | The real console, owner only, behind Access and the passkey | Next, for private remote use (OC-092) |
| C. Capped hosted sandbox | Per-visitor microVM or container, egress deny, spend cap, quotas, abuse controls | Only for a real launch; needs a threat model and a cost cap |
