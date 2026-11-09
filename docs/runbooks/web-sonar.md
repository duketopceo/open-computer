# Sonar search (OpenRouter)

Opt-in web search through Perplexity's Sonar models on OpenRouter. Fits the "100% OpenRouter" rule: one key, citations on the response.

| Env | Default | Meaning |
| --- | --- | --- |
| `OC_SEARCH_PROVIDER` | unset | Set to `sonar` to use Sonar for search (fetch stays host HTTP + allowlist) |
| `OC_WEB_PROVIDER` | unset | `sonar` is an alias for the same search path |
| `OC_SONAR_MODEL` | `perplexity/sonar` | Also useful: `perplexity/sonar-pro` (more citations, higher cost) |

Requires `OPENROUTER_API_KEY` (Settings → OpenRouter). Citations from the API become the step's Sources list.

## SPACE vs Sonar (do not confuse)

- **SPACE** in Open Computer (`OC_EXECPLANE_BACKEND=space`) is Perplexity's open **sandbox** runtime (`perplexityai/space`, Firecracker microVMs). It is not search.
- **Sonar** is Perplexity's **search/answer** model family on OpenRouter.

## Cost note (check OpenRouter for live prices)

`perplexity/sonar` is the cheap default. Pro / reasoning / deep-research cost more and may add per-search fees. Prefer `sonar` for routine lookup; escalate model only when needed.
