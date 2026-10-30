"""Agent-orchestrator slice: a planner loop that spawns real agent-harness workers over ACP.

  loop.AgentLoop      parent: plan -> spawn worker -> read result -> re-plan, under a budget
  worker.AgentWorker  the pluggable worker interface (OpenCode first, via `ori opencode acp`)
  policy.classify     deterministic allow/ask/refuse for every tool call a worker asks permission for
  trail.Trail         append-only job trail the console reads
"""
