"""Temporal schedules behind /automations (F4).

An automation is a goal on a cadence: every fire starts a GoalCronWorkflow
that submits a real goal run and drives it to terminal state — visible in
/work, same fsproto state, same audit. Listing/mutation go through the
schedule API; nothing here touches a run's on-disk state directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import timedelta

_PREFIX = "auto-"
SID_RE = r"auto-[0-9a-f]{8}"


def enabled() -> bool:
    try:
        from services.durable import client as durable
    except ImportError:
        return False
    return durable.enabled()


def _host_ns() -> tuple[str, str]:
    return (
        os.environ.get("OC_TEMPORAL_HOST", "127.0.0.1:7233"),
        os.environ.get("OC_TEMPORAL_NAMESPACE", "default"),
    )


async def _client():
    from temporalio.client import Client

    host, ns = _host_ns()
    return await Client.connect(host, namespace=ns)


def _spec_text(spec) -> str:
    """Human cadence from a ScheduleSpec — intervals or cron, literal."""
    ivs = getattr(spec, "intervals", None) or []
    if ivs:
        s = int(ivs[0].every.total_seconds())
        if s % 86400 == 0:
            return f"every {s // 86400}d"
        if s % 3600 == 0:
            return f"every {s // 3600}h"
        return f"every {s // 60}m" if s % 60 == 0 else f"every {s}s"
    crons = getattr(spec, "cron_expressions", None) or []
    if crons:
        return f"cron {crons[0]}"
    return "unspecified"


def _goal_of(act) -> str:
    """Decode the GoalCronInput payload — the schedule's real goal text."""
    for source in (
        lambda: json.loads(act.args[0].data).get("goal", ""),
        lambda: json.loads(getattr(act, "static_summary", b"").data),
    ):
        try:
            got = source()
            if got:
                return str(got)
        except Exception:
            continue
    return ""


async def _list() -> list[dict]:
    client = await _client()
    out = []
    async for s in await client.list_schedules():
        if not s.id.startswith(_PREFIX):
            continue  # only schedules this console created
        d = await client.get_schedule_handle(s.id).describe()
        act = d.schedule.action
        goal = _goal_of(act)
        state = d.schedule.state
        out.append(
            {
                "id": s.id,
                "goal": goal,
                "every": _spec_text(d.schedule.spec),
                "paused": bool(state and state.paused),
                "note": (state.note if state else "") or "",
                "next": [t.timestamp() for t in (d.info.next_action_times or [])][:3],
                "fired": len(d.info.recent_actions or []),
            }
        )
    return out


def list_automations() -> list[dict] | None:
    """None when disabled/unreachable — the page renders the outage literally."""
    if not enabled():
        return None
    try:
        return asyncio.run(_list())
    except Exception:
        return None


async def _create(goal: str, every_s: int) -> str:
    from temporalio.client import (
        Schedule,
        ScheduleActionStartWorkflow,
        ScheduleIntervalSpec,
        ScheduleSpec,
        ScheduleState,
    )

    from services.durable.workflows import TASK_QUEUE, GoalCronInput, GoalCronWorkflow

    client = await _client()
    sid = _PREFIX + uuid.uuid4().hex[:8]
    await client.create_schedule(
        sid,
        Schedule(
            action=ScheduleActionStartWorkflow(
                GoalCronWorkflow.run,
                GoalCronInput(goal=goal, by="automation"),
                id=f"cron-{sid}",
                task_queue=TASK_QUEUE,
                static_summary=goal[:120],
            ),
            spec=ScheduleSpec(
                intervals=[ScheduleIntervalSpec(every=timedelta(seconds=every_s))]
            ),
            state=ScheduleState(note=f"every {every_s // 60}m"),
        ),
    )
    return sid


def create(goal: str, every_minutes: int) -> str:
    """Create a schedule; returns its id. Raises on Temporal errors."""
    return asyncio.run(_create(goal, every_minutes * 60))


def set_paused(sid: str, paused: bool) -> None:
    async def go():
        h = (await _client()).get_schedule_handle(sid)
        if paused:
            await h.pause(note="paused from console")
        else:
            await h.unpause(note="resumed from console")

    asyncio.run(go())


def trigger(sid: str) -> None:
    async def go():
        await (await _client()).get_schedule_handle(sid).trigger()

    asyncio.run(go())


def delete(sid: str) -> None:
    async def go():
        await (await _client()).get_schedule_handle(sid).delete()

    asyncio.run(go())
