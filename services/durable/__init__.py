"""Durable execution via Temporal (OC-4 / U15).

fsproto remains the state protocol — Temporal owns the drive loop's
scheduling, retries, and crash-resume. Each activity is one honest
orchestrator sweep against the same on-disk run state; a workflow
restart replays scheduling, never state.

Temporal is optional: unavailable -> goalruns falls back to the
in-process driver (which now also resumes across restarts).
"""
