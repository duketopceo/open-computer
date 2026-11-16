# Cloud core: one-command setup, tests and update

The cloud core is a Worker with Durable Objects, a Workflow, D1, R2, Secrets Store and a Container sandbox, deployed
into your own Cloudflare account. Nothing is hosted for you.

## What you need

| Need | Why |
| --- | --- |
| Cloudflare account on the Workers Paid plan | Containers and Durable Objects require it; setup stops and says so if missing |
| `wrangler login`, or `CLOUDFLARE_API_TOKEN` | Workers, D1, R2, Secrets Store and Containers write access |
| Docker running locally | Wrangler builds the sandbox image on your machine (linux/amd64) |
| An OpenRouter key | Passed once through the environment as `OC_OPENROUTER_KEY`; stored only in Secrets Store |

## Setup

```sh
cd core && pnpm install
export OC_OPENROUTER_KEY=...        # from your keyring; never put it on a command line
pnpm --filter @open-computer/diagnostics exec node src/cli.ts setup
```

Setup is the same chain the web page shows, in order: Cloudflare account and token, plan and features, create and bind
resources and deploy (safe to run again), store keys, backend health, OpenRouter checks, speed and latency, sandbox,
memory, audit and spend. A failing row says why in plain words, what to do, and can be retried. Each row shows its time.

State kept on your machine (mode 0600): `~/.config/open-computer/cloud.json` with the core address and the access token.
Set `OC_CLOUD_URL` and `OC_CLOUD_TOKEN` to override. The rendered deploy config (`wrangler.deploy.jsonc`) holds your ids
and is git-ignored.

## Tests

- Web: open the core's address in a browser, paste the access token, press Run tests. Export results as JSON.
- CLI: `node src/cli.ts doctor [--json results.json]`. Exit code 0 only if every row passes.
- Add `?autorun=1` to the page address to start the run as soon as it loads (token read from the browser session).

The model rows cost a fraction of a cent and count toward the daily cap (default $5, adjustable in Settings).

## Protection

Every `/v1` route needs the bearer access token; wrong tokens are throttled. The setup page itself is static and holds
no data. For a stronger front door, put Cloudflare Access in front of the hostname and keep the token as a second factor.
The `workers.dev` address is created without preview URLs.

## Sandbox image

`cloudflare/sandbox` contains only the `sandbox-shim` helper, so the image copies it onto `debian:bookworm-slim` and
installs `curl` and certificates. Containers start with internet off and an allowlisted environment, and all HTTPS goes
through the outbound handler. If you switch an existing deployment between Container scheduling policies, delete the old
container application first (`wrangler containers delete <id>`); Wrangler cannot convert one in place.

## Remove

`wrangler delete --name open-computer-core`, `wrangler containers delete <id>`, then the D1 database
(`open-computer`), the R2 bucket (`open-computer-artifacts`) and the two `oc_` secrets in your Secrets Store.
