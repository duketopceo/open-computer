# v1 core: desktop app and Cloudflare core

Status: first slice built (PR 1 of `docs/plans/2026-11-15-0337-feat-oc-v1-desktop-cloudflare-core-plan.md`). The Python services beside it are frozen, not removed. **Nothing in this folder deploys.** No command below contacts a Cloudflare account, creates a resource or calls a live model.

## What is here

| Path | What it is |
|---|---|
| `core/packages/agent-core` | The platform-neutral agent core: event contract, OpenRouter streaming provider, the step function and loop, approval queue, spend caps, audit log with a hash chain, memory tiers, sandbox environment guard. No Cloudflare or Node API inside. |
| `core/packages/local` | The local core sidecar the desktop app launches: loopback HTTP, bearer token on stdin, three-piece Test connection. Compiles to one binary. |
| `core/packages/cloud` | The Cloudflare deployment of the same core as code: Worker, Session Durable Object, ledger Durable Object, Workflow driver, D1 and R2 adapters, Container sandbox, AI Gateway and Secrets Store bindings. |
| `core/contracts` | The event JSON Schema, and the local API fixtures that both the TypeScript server tests and the Rust client tests assert. |
| `desktop` | The Rust desktop shell (Rune UI): onboarding screens and the pure `shell-core` crate behind them. |

How they connect: the desktop talks to its local core over loopback, and the local core talks to the cloud core. Tasks run in the cloud core, so they keep going with the desktop closed. A task is a Workflow instance; each agent step is a Workflow step; an approval is a wait for an event.

## Run the tests

From `core/` (Node 24 and pnpm; bun only for the sidecar build):

```sh
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test                                        # agent-core and local under Node, cloud under miniflare
pnpm --filter @open-computer/local build         # compiles the sidecar and smoke-checks it
pnpm --filter @open-computer/cloud exec wrangler deploy --dry-run --outdir dist   # bundle check only
```

From `desktop/` (Rust stable; Linux needs `pkg-config`, OpenSSL and the X11 and Wayland client headers):

```sh
cargo fmt --check
cargo clippy --workspace -- -D warnings
cargo test -p shell-core                         # no display or GPU needed
cargo check --workspace
cargo run -q -- --print-state                    # onboarding state as JSON, no window
OC_CORE_BIN=../core/packages/local/out/oc-core cargo test -p shell-core --test e2e -- --ignored
```

Tests never touch the network or a real model: the provider tests replay SSE cassettes under `core/packages/agent-core/test/fixtures/openrouter/`, which are hand-authored from the documented chunk shapes and labelled synthetic. A live capability probe is a later, owner-run step with a spend cap.

## Run the Worker locally

`wrangler dev` runs the Worker, Durable Objects, Workflow, D1 and R2 on your machine with local storage. Containers need Docker locally and are the one piece the miniflare tests cannot cover; a local `wrangler dev` run with Docker is the optional way to see a real sandbox start. Never run `wrangler deploy` without `--dry-run` unless you mean to deploy, and the one-step deploy that does that for users is a later unit (U16).

`wrangler.jsonc` holds inert placeholders only (all-zero database id, placeholder store id, empty gateway account). No account id is committed, and a hygiene test fails if a 32-hex id appears in `core/` or `desktop/`.

## Secrets

- Cloud: the access secret and the model key live in Secrets Store bindings (open beta), read only through `core/packages/cloud/src/models/secrets.ts`. A key in a task body is rejected, never used.
- Desktop: the shell owns the operating-system keyring and hands values to the local core in memory. The UI shows only whether a key is set.
- Sandbox: environments are built from an allowlist. An outbound handler outside the sandbox adds credentials for a minimal host list that never includes a model-provider host, and audits every credentialed request. In the first slice the list is empty: every sandbox request is denied and audited.

## Known gaps in this slice

- Containers and the outbound interception over HTTPS are unproven until an owner-approved real run; the port and handler are tested with fakes.
- The AI Gateway route shape follows Cloudflare's documented pattern and is confirmed in the first real run.
- R3 is scaffolded: the desktop is built and tested on Linux in CI; macOS and Windows arrive with packaging.
- Connect Cloudflare takes the address and access secret of an already deployed core. The guided token step and one-step deploy are U16.
