"""Runtime map: plain-language step locations for Settings (no local models)."""

from services import runtime_map as rm


def test_status_lists_openrouter_and_host_web_without_local_models():
    s = rm.status()
    assert "OpenRouter" in s["summary"]
    ids = [step["id"] for step in s["steps"]]
    assert ids == ["chat", "model", "web", "sandbox", "device", "agent"]
    blob = " ".join(f"{x['title']} {x['where']} {x['detail']}" for x in s["steps"]).lower()
    assert "openrouter" in blob
    assert "local" not in blob  # Luke: 100% OpenRouter — no local-model placement
    assert "llama" not in blob and "ollama" not in blob
    web = next(x for x in s["steps"] if x["id"] == "web")
    assert "host" in web["where"].lower()
    assert "not a sandbox" in web["where"].lower()
    assert s["docs"].endswith("runtime.md")
