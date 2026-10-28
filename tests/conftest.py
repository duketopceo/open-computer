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


class RealProviderCall(AssertionError):
    pass


def is_real_provider(url: str) -> bool:
    return "openrouter.ai" in url


@pytest.fixture(autouse=True)
def _no_real_openrouter(monkeypatch):
    """Belt and braces on top of "no ambient key": any attempt to open a connection to openrouter.ai from a test
    fails the test loudly, whatever base URL or key the code under test ended up with."""
    import urllib.request

    real = urllib.request.urlopen

    def guarded(req, *a, **kw):
        url = req if isinstance(req, str) else getattr(req, "full_url", "")
        if is_real_provider(url):
            raise RealProviderCall(f"test tried to call the real OpenRouter: {url.split('?')[0]}")
        return real(req, *a, **kw)

    monkeypatch.setattr(urllib.request, "urlopen", guarded)
    return is_real_provider
