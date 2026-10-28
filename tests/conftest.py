import pytest


@pytest.fixture(autouse=True)
def _isolated_obs(tmp_path, monkeypatch):
    """Observability state never touches the developer's home dir, and tests start from default config."""
    monkeypatch.setenv("OC_OBS_DIR", str(tmp_path / "obs"))
    # Tests must never spend real money or reach a real provider: no ambient key, no ambient base URL.
    for var in ("OPENROUTER_API_KEY", "OPENROUTER_BASE"):
        monkeypatch.delenv(var, raising=False)
    for var in ("OC_LOG_DIR", "OC_DEBUG", "OC_LOG_LEVEL", "OC_OBS_CAPTURE_CONTENT", "OC_OPENROUTER_ATTRIBUTION",
                "OC_OPENROUTER_REFERER", "OC_OPENROUTER_TITLE", "OC_OPENROUTER_CATEGORIES",
                "OC_OPENROUTER_APP_VISIBILITY", "OC_OPENROUTER_USER", "OC_OPENROUTER_ENVIRONMENT",
                "OC_OPENROUTER_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    yield
