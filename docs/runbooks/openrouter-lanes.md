# OpenRouter keys per lane, and chat first

Every OpenRouter call runs in one of three lanes, highest priority first:

| Lane | What | Key (falls back to `OPENROUTER_API_KEY`) |
| --- | --- | --- |
| `chat` | the orchestrator answering you, and compaction scoring on that turn | `OPENROUTER_API_KEY_CHAT` |
| `subagents` | run work: planning, routing decisions, steps (default) | `OPENROUTER_API_KEY_SUBAGENTS` |
| `background` | the durable worker (automations) | `OPENROUTER_API_KEY_BACKGROUND` |

One key still works: set only `OPENROUTER_API_KEY` and every lane uses it. Each call records its `lane`.

What separate keys buy: OpenRouter-side spend caps and usage per key, so automations can be capped or cut off
without touching chat, and the bill shows which lane spent what. What they do not buy: more rate limit. OpenRouter
limits by account, not key.

What stops crowding is local: at most `OC_OPENROUTER_CONCURRENCY` (default 6) calls are in flight per process, and
subagent plus background calls may hold at most that minus `OC_OPENROUTER_CHAT_RESERVE` (default 2), so a chat call
always has a free slot. When calls queue, chat goes first, then subagents, then background; first come first served
within a lane. Slots are per process; the durable worker is its own process with its own slots.

Code: `services/openrouter/lanes.py`; the key is picked and the slot taken in `services/openrouter/client.post`.
