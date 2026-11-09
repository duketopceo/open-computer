# Web search / fetch providers

Host-side (`services/orchestrator/tools/web.py`). Egress allowlist and private-IP checks always apply. Not run inside a sandbox unless Luke asks later.

| `OC_WEB_PROVIDER` | Search / fetch | Notes |
| --- | --- | --- |
| (unset) | BrowserOS if `OC_BROWSER_MCP_URL`, else hosted `OC_SEARCH_API_URL` | Existing defaults |
| `browseros` | BrowserOS neo MCP (`OC_BROWSER_MCP_URL`, default `http://127.0.0.1:9010/mcp`) | Owner's browser |
| `hosted` | `OC_SEARCH_API_URL` (+ optional `OC_SEARCH_API_KEY`); HTTP fetch for pages | |
| `scrapling` | Scrapling MCP (`OC_SCRAPLING_MCP_URL`, default `http://127.0.0.1:8000/mcp`) | Off unless set. Uses `make_request` only |
| `sonar` | OpenRouter Perplexity Sonar for search (see below when shipped) | 100% OpenRouter |

## Scrapling (optional)

1. Run Scrapling's MCP with HTTP transport on the host (example: `scrapling mcp` / docs at https://scrapling.readthedocs.io/en/latest/ai/mcp-server.html).
2. Set `OC_WEB_PROVIDER=scrapling`.
3. Optional: `OC_SCRAPLING_MCP_URL`, `OC_SCRAPLING_STEALTH=1` (enables `browser_fetch_once`; **off by default** — do not use stealth against third-party sites without a clear right to access).

Stealth / Cloudflare-bypass tools are product-risky; leave them off for normal chat.
