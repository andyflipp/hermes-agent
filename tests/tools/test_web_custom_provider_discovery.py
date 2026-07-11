"""Regression tests for lazy discovery of profile-local web providers."""

from tools import web_tools


def test_shared_backend_discovers_configured_custom_provider(monkeypatch):
    state = {"loaded": False, "calls": 0}
    provider = object()

    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"backend": "local-fetch"})

    def ensure_loaded():
        state["loaded"] = True
        state["calls"] += 1

    def registered(name):
        return provider if state["loaded"] and name == "local-fetch" else None

    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", ensure_loaded)
    monkeypatch.setattr(web_tools, "_registered_web_provider", registered)

    assert web_tools._get_backend() == "local-fetch"
    assert state["calls"] == 1


def test_extract_backend_discovers_configured_custom_provider(monkeypatch):
    state = {"loaded": False, "calls": 0}

    monkeypatch.setattr(
        web_tools,
        "_load_web_config",
        lambda: {"extract_backend": "local-fetch", "backend": "ddgs"},
    )

    def ensure_loaded():
        state["loaded"] = True
        state["calls"] += 1

    monkeypatch.setattr(web_tools, "_ensure_web_plugins_loaded", ensure_loaded)
    monkeypatch.setattr(
        web_tools,
        "_is_backend_available",
        lambda name: state["loaded"] and name == "local-fetch",
    )

    assert web_tools._get_capability_backend("extract") == "local-fetch"
    assert state["calls"] == 1
