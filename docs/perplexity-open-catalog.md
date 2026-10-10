# Perplexity's open-source catalog (as of 2026-10-10)

Everything below was verified downloadable and ungated. The real Hugging Face
org is **`perplexity-ai`** (`huggingface.co/perplexity-ai`); the no-hyphen
`perplexityai` is an unrelated squatter account. GitHub org:
`github.com/perplexityai` (54 repos).

## Decision models — the router layer

| Model | License | Notes |
|---|---|---|
| `pplx-decider-v1-27b` | Apache-2.0 | Fine-tuned from Qwen3.8-27B. Choice + yes/no prediction with calibrated probabilities, multimodal. Model card benches **85.71 vs Jev's 84.51**. Ships `inference.py` (`Decider` class) **and the full training source** (`autojev/`: train.py, sft_pipeline.py, evaluate.py, synthetic.py, server.py, configs). ~49 GiB. |
| `pplx-decider-v1.1-27b` | Apache-2.0 (presumed) | Newer version |

## Embedding models — the retrieval / local-index layer

| Model | License | Notes |
|---|---|---|
| `pplx-embed-v2-late-0.6b` / `-9b` | MIT | ColBERT-style late-interaction, multimodal (text, images, rendered PDF pages, no OCR). **Shared embedding space: index with 9B, query with 0.6B at 0.6B cost.** 9B hits 92.4% on MADQA. Distilled from an internal 18B ColBERT teacher. Needs sentence-transformers>=6.0, transformers>=5.4. Self-host only; hosted API planned, not live. |
| `pplx-embed-v2-context-9b-preview` | MIT | Contextual RAG embeddings (each chunk embedded with full document in view). Preview — may change. |
| `pplx-embed-v1-0.6b` / `v1-4b`, contextual + late v1 variants | MIT | First-gen dense + contextual embeddings. |

## PII models — the privacy / escalation gate

| Model | License | Notes |
|---|---|---|
| `PII-Tracer` (+ `-vLLM`, `-GGUF`, `-MLX`) | MIT | ~600M bidirectional Qwen3 encoder. Two heads: token classification (BIOES over 9 PII categories: person, account number, URL, date, address, email, phone, other_pii, secret) with constrained Viterbi decoding, plus conversation-level sensitivity classifier. Paper: PII-TRACE (arXiv:2609.22200). `predict()` / `mask()` API. |
| `pplx-pii-masking-vllm` | (same family) | Earlier 0.6B masking model; identified in press as the classifier behind the Hybrid Compute on-device privacy gate. |

## Safety

| Model | License | Notes |
|---|---|---|
| `browsesafe` | MIT | Qwen3-30B-A3B MoE finetune for prompt-injection detection in HTML. **Caveat:** Lasso Security red-team found a 36% bypass rate with encoding tricks (Jan 2026) — one layer, not the layer. |

## Portable Computer's local models

- `pplx-computer-qwen-3-8-27b-dflash2-*` (GGUF + vLLM builds) — the actual 27B builds shipped in Portable Computer
- `pplx-computer-qwen-3-6-35b-a3b-*` (MLX / NVFP4) — 35B MoE builds
- Useful as reference targets; Perplexity claims the benchmark wins came from the harness, not the weights.

## Datasets & benchmarks (all open)

| Dataset | What it is |
|---|---|
| `browsesafe-bench` | 14,719 prompt-injection examples: 11 attack types × 9 placements × 3 styles |
| `PII-TRACE` | Context-aware PII detection benchmark for multi-turn conversations |
| `draco` | Deep Research Accuracy, Completeness, Objectivity — co-developed with Harvard |
| `wandr` | Wide ANd Deep Research — 500 realistic data-collection tasks + eval harness |

## GitHub — agent-relevant repos

| Repo | License | What it is |
|---|---|---|
| `perplexityai/numbat` | Apache-2.0 | Endpoint agent monitoring: single static Go binary, hooks/OTLP/log exporters normalized into one event model, **CEL rule engine** (built-in + custom YAML, multi-step sequence rules), opt-in pre-action blocking (off by default), forensic reconstruction, versioned NDJSON records. |
| `perplexityai/bumblebee` | Apache-2.0 | Read-only supply-chain scanner (pure Go, zero deps): inventories package metadata, lockfiles, extensions, **MCP configs / AI tool configs** — "which dev machines are exposed to this advisory right now?" NDJSON for SIEM/MDM. |
| Official MCP server for the Perplexity API Platform | MIT | Reference MCP integration (TypeScript) |
| Inference technology garden | MIT | Rust inference infra collection |

## Still proprietary (the moat)

- **SPACE** — no open code; architecture described in the "Making SPACE" blog post only
  (perplexity.ai/hub/blog/making-space-secure-and-efficient-runtimes-for-long-running-agents).
  Median sandbox creation 60ms (P90 89ms), millions of creations/week.
- **Local Knowledge Work Bench** — their 53-task internal agent benchmark (Portable 82.6%
  vs Pi 77.6% vs Hermes 74.0%). Perplexity says it plans to open-source it; not out as of today.
- The orchestrator/router product logic, Model Council synthesis, audit-log pipeline,
  improvement-loop automation.

## Safety post reference

"How we engineer safer agents" (perplexity.ai/hub/blog/how-we-engineer-safer-agents,
Sep 29 2026) defines six layers: Guidance (proprietary) → Input screening
(**open**: BrowseSafe) → Containment (**proprietary**: SPACE) → Independent
monitoring (**open**: Numbat) → Improvement loop (internal; inputs Numbat+Bumblebee
are open) → Adversarial testing (findings published; 9 frontier models failed to
escape SPACE with root).
