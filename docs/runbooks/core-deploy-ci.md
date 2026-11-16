# Core deploy from CI (Cloudflare)

The `deploy core (cloudflare)` job in `.github/workflows/ci.yml` deploys the Cloudflare core after `production approval`
(push to `main` only, `production` environment, reviewer `duketopceo`). It replaces nothing: the legacy SSH job
`deploy (production)` stays behind `vars.OC_LEGACY_DEPLOY`.

## What it runs

1. The guard step checks that every secret and variable below is set. If any is missing the job succeeds with a notice that
   lists the missing names (never values) and deploys nothing.
2. `node src/cli.ts setup --skip-model` in `core/packages/diagnostics`: the same deploy-as-code chain the wizard uses. It
   finds or creates the D1 database, R2 bucket and Secrets Store entries, renders `wrangler.deploy.jsonc` from the committed
   `core/packages/cloud/wrangler.jsonc` (real ids filled in, never committed; `AI_GATEWAY_ID` and the Workers AI binding come
   from `OC_AI_GATEWAY_ID`), applies D1 migrations, runs `wrangler deploy`, and runs the diagnostics rows.
3. `node src/cli.ts doctor --skip-model`: the diagnostics chain again against the deployed core as the gate. Exit code 1 on
   any failed or skipped row, which fails the job. The OpenRouter model rows are skipped on purpose: CI holds no model key and
   spends nothing.
4. The two JSON reports are kept as the `core-deploy-results` artifact.

## What the owner adds (Settings, Secrets and variables, Actions; or the `production` environment)

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `CLOUDFLARE_API_TOKEN` | An API token with the permissions below |
| Secret | `CLOUDFLARE_ACCOUNT_ID` | The Cloudflare account id |
| Secret | `OC_CLOUD_TOKEN` | The core's access token (the `token` saved by your first local `oc setup`). The diagnostics call the core's `/v1` routes with it. CI cannot read it back from Secrets Store. `setup` creates one only when none exists and never changes it on a later run (a test holds that); to issue a new one on purpose run `node src/cli.ts setup --rotate-token`, then update this secret, the desktop picks the new one up from `cloud.json` by itself, and any other computer needs it copied from there |
| Variable | `OC_AI_GATEWAY_ID` | The AI Gateway name (`open-computer` unless you chose another) |

One-time before the first CI deploy: run `oc setup` once on your own machine with `OC_OPENROUTER_KEY` set, so the OpenRouter
key is already in the account's Secrets Store. CI never holds that key, and the `keys` row fails if it is absent.

## API token permissions

Create the token at My Profile, API Tokens, Create Custom Token. Scope it to the one account, no zone access, and set an
expiry. These match the scopes the wizard's browser sign-in requests (`REQUIRED_SCOPES` in
`core/packages/diagnostics/src/signin.ts`).

| Level | Permission | Access | Used for |
| --- | --- | --- | --- |
| Account | Workers Scripts | Edit | `wrangler deploy`, Durable Object migrations, the Workflow |
| Account | D1 | Edit | find or create the database, apply migrations |
| Account | Workers R2 Storage | Edit | find or create the artifacts bucket |
| Account | Secrets Store | Edit | find or create the store, the access secret, bind secrets |
| Account | Containers (older dashboards: Cloudchamber) | Edit | the sandbox container application and image |
| Account | Account Settings | Read | account lookup |
| User | User Details | Read | `wrangler whoami` |
| User | Memberships | Read | `wrangler whoami` account listing |

AI Gateway needs no permission here: the core reaches it through the Workers AI binding as the Worker, and the gateway is
created once in the dashboard (see `cloud-setup.md`). The list was derived from Wrangler's documented requirements and the
repo's sign-in scopes, and was not run against a live account. If the first run reports a missing permission, Wrangler names
it; add that one and re-run.

## Rules

- No secret is printed. The guard prints names only, and Wrangler's output goes through `scrub` in `control.ts`.
- The job never runs on `pull_request`, and the secrets are not available to pull request jobs.
- Rollback: `oc update` already rolls back on a failed health check. For a bad deploy that passed health, use Workers,
  Deployments, Rollback in the dashboard.
