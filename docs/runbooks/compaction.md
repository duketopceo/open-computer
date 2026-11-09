# Long chats: compaction (jev-compact)

A chat thread sends its whole history to the orchestrator model on every turn. Once that history is larger than
`OC_COMPACT_BUDGET` tokens (estimated at 4 characters a token), Open Computer compacts the older part with
[jev-compact](https://github.com/duketopceo/jev-compact) (MIT, a pinned dependency) before the call. Short chats are
untouched and cost nothing extra.

How it works:

1. Every message is a span (`s0`, `s1`, ... in thread order, so ids stay put as the chat grows). The last
   `OC_COMPACT_TAIL` messages are always sent as themselves.
2. jev-compact works out what the chat is about now (the moving highlight) and each older span is scored for
   relevance and for still-needed facts. The scorer is a cheap model: the catalog's Low effort preset, or
   `OC_COMPACT_MODEL`. Calls go through Open Computer's OpenRouter client as kind `compact`, at temperature 0, so
   they are attributed, costed and traced like every other call. With no `OPENROUTER_API_KEY`, or when a call fails
   or answers garbage, that span gets jev-compact's offline heuristic.
3. Spans with a code fence or an error signature (traceback, `SomethingError:`, `FAILED`, non-zero exit) are pinned and
   always kept verbatim, whatever the model says.
4. The best spans are kept verbatim under the budget; runs of the rest become one-line tombstones such as
   `[[s4-s9 tombstoned · 6 span(s) · ...]]`. If the result is still over budget, the head gets a smaller share and is
   compacted again (scores are cached, so that costs no calls). Pinned spans and the tail are never dropped, so a
   thread made only of those can still overshoot.
5. Scores are cached per thread by (highlight, span, model): replaying the same thread gives the same context and
   makes no new calls.

Restore: every span is saved under `$OC_THREADS_DIR/_compact/<thread>/`. The orchestrator can answer
`{"action":"restore","spans":"s4-s9"}` to see a tombstoned range verbatim before it answers (at most 2 per message;
a restore does not use up a run). By hand: `python -m services.compact restore <thread> s4-s9`.

| Env | Default | Meaning |
| --- | --- | --- |
| `OC_COMPACT` | on | `off` sends full history, as before |
| `OC_COMPACT_BUDGET` | `24000` | Token target for one orchestrator call (min 1000) |
| `OC_COMPACT_TAIL` | `6` | Newest messages always sent as themselves |
| `OC_COMPACT_MODEL` | Low preset | Scoring model id |
| `OC_COMPACT_MAX_SCORED` | `60` | Spans scored by the model per compaction, newest first; older ones use the heuristic |

Scope: chat threads. Run workers get short, self-contained goals and are not compacted.
