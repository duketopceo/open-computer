# Where work runs

Open Computer never runs models locally: every chat and worker model call goes through **OpenRouter**.
What changes per step is *where tools execute* and whether a disposable sandbox is involved.

| Kind | Where it runs | Notes |
| --- | --- | --- |
| Chat reply | Host → OpenRouter | Orchestrator picks reply, ask, or run |
| Model step | Host → OpenRouter | `cloud-OpenRouter` placement |
| Web search / page read | Host (`services/orchestrator/tools/web.py`) | Egress allowlist + private-IP block; not in a sandbox |
| Code / shell | MicroVM via `services/execplane/` | CubeSandbox default; `OC_EXECPLANE_BACKEND=space` uses Perplexity SPACE (Firecracker). Fail-closed if the VM will not boot |
| Device step | Your registered machine | `services/device_agent/` with grants |
| Long agent job | ACP harness inside a microVM | `services/agents/`; approvals in the console |

## Isolation today

- Egress deny-by-default; human-applied allowlist (`services/execplane/policy.py`) plus a small built-in set for search and OpenRouter.
- PII gate before cloud-sensitive paths.
- Credentials are not supposed to enter the guest (credential boundary still partial — see ROADMAP).
- Web fetch stays on the host by design until Luke asks otherwise.

## Related env

| Env | Meaning |
| --- | --- |
| `OC_EXECPLANE_BACKEND` | `cube` (default) or `space` |
| `OC_WEB_PROVIDER` | Web tool backend (`browseros`, `hosted`, `scrapling`, …) |
| `OC_EGRESS_ALLOW` | Extra exact hostnames the owner approves |

Console: Settings → Runtime. API: `GET /api/runtime`.
