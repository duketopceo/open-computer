"""Stage 2 guard rails: the services keep using the shared logger, and nobody sneaks `print()` back in."""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = ("orchestrator", "router", "placement", "gate", "cost", "execplane", "device", "device_relay",
            "device_agent", "mcp", "opsview")  # workspace routes are served by opsview, so its access log covers them

# Command-line entry points whose stdout *is* their interface (a table, a minted token, a build summary).
PRINT_OK = {
    "oc/obs/tail.py",
    "services/openrouter/__main__.py",
    "services/opsview/auth.py",
    "services/workspace/fixtures/build.py",
}


def py_files():
    for top in ("oc", "services"):
        for p in sorted((ROOT / top).rglob("*.py")):
            if "__pycache__" not in p.parts:
                yield p


def test_no_bare_print_in_services_or_oc():
    bad = []
    for p in py_files():
        rel = str(p.relative_to(ROOT))
        if rel in PRINT_OK:
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
                bad.append(f"{rel}:{node.lineno}")
    assert bad == [], "use oc.obs.get_logger(...) instead of print(): " + ", ".join(bad)


def test_print_allowlist_has_no_stale_entries():
    assert all((ROOT / rel).exists() for rel in PRINT_OK)


def test_every_service_logs_through_oc_obs():
    for name in SERVICES:
        files = list((ROOT / "services" / name).rglob("*.py"))
        assert any("get_logger(" in f.read_text() for f in files), f"services/{name} never calls get_logger"
    assert "get_logger(" in (ROOT / "services" / "telemetry.py").read_text()


def test_logging_stays_stdlib_only():
    third_party = {"structlog", "loguru", "sentry_sdk", "opentelemetry"}
    for p in py_files():
        imported = {n.names[0].name.split(".")[0] for n in ast.walk(ast.parse(p.read_text())) if isinstance(n, ast.Import)}
        imported |= {n.module.split(".")[0] for n in ast.walk(ast.parse(p.read_text()))
                     if isinstance(n, ast.ImportFrom) and n.module}
        assert not (imported & third_party), p
