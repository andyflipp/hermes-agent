"""Tests for gateway/hooks.py — event hook system."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.hooks import HookRegistry, resolve_ingress_hook_decision


def _create_hook(hooks_dir, hook_name, events, handler_code):
    """Helper to create a hook directory with HOOK.yaml and handler.py."""
    hook_dir = hooks_dir / hook_name
    hook_dir.mkdir(parents=True)
    (hook_dir / "HOOK.yaml").write_text(
        f"name: {hook_name}\n"
        f"description: Test hook\n"
        f"events: {events}\n"
    )
    (hook_dir / "handler.py").write_text(handler_code)
    return hook_dir


class TestHookRegistryInit:
    def test_empty_registry(self):
        reg = HookRegistry()
        assert reg.loaded_hooks == []
        assert reg._handlers == {}


class TestIngressDecision:
    def test_ignores_unhandled_and_unknown_shapes(self):
        assert resolve_ingress_hook_decision([
            None,
            "notification result",
            {"handled": False, "response": "no"},
            {"notification": "complete"},
        ]) is None

    def test_accepts_one_strict_handled_response(self):
        assert resolve_ingress_hook_decision([
            {"handled": True, "response": "  accepted  "},
        ]) == "accepted"

    @pytest.mark.parametrize(
        ("status", "writes"),
        [
            ("CREATED_READBACK_PASS", 1),
            ("DUPLICATE_NO_WRITE", 0),
            ("EXCEPTION_NO_WRITE", 0),
            ("UNCERTAIN_NO_RETRY", 0),
        ],
    )
    def test_accepts_exact_terminal_orchestration_response(self, status, writes):
        result = {
            "handled": True,
            "status": status,
            "writes": writes,
            "reply_mode": "SAME_THREAD",
            "user_message": "  Безопасный итог.  ",
        }

        assert resolve_ingress_hook_decision([result]) == "Безопасный итог."
        assert set(result) == {
            "handled", "status", "writes", "reply_mode", "user_message",
        }

    @pytest.mark.parametrize(
        "malformed",
        [
            {"handled": True, "status": "UNKNOWN", "writes": 0,
             "reply_mode": "SAME_THREAD", "user_message": "x"},
            {"handled": True, "status": "CREATED_READBACK_PASS", "writes": 0,
             "reply_mode": "SAME_THREAD", "user_message": "x"},
            {"handled": True, "status": "DUPLICATE_NO_WRITE", "writes": True,
             "reply_mode": "SAME_THREAD", "user_message": "x"},
            {"handled": True, "status": "EXCEPTION_NO_WRITE", "writes": 0,
             "reply_mode": "NEW_THREAD", "user_message": "x"},
            {"handled": True, "status": "UNCERTAIN_NO_RETRY", "writes": 0,
             "reply_mode": "SAME_THREAD", "user_message": ""},
            {"handled": True, "status": "DUPLICATE_NO_WRITE", "writes": 0,
             "reply_mode": "SAME_THREAD", "user_message": "x", "raw_id": "secret"},
            {"handled": True, "status": "DUPLICATE_NO_WRITE", "writes": 0,
             "reply_mode": "SAME_THREAD"},
            {"handled": True, "status": {}, "writes": 0,
             "reply_mode": "SAME_THREAD", "user_message": "x"},
            {"handled": True, "status": "DUPLICATE_NO_WRITE", "writes": 0,
             "reply_mode": "SAME_THREAD", "user_message": "x" * 257},
            {"handled": True, "response": "x", "extra": True},
        ],
    )
    def test_malformed_handled_output_fails_closed(self, malformed):
        response = resolve_ingress_hook_decision([malformed])

        assert response is not None
        assert "остановлена" in response
        assert "secret" not in response

    def test_multiple_claims_fail_closed(self):
        response = resolve_ingress_hook_decision([
            {"handled": True, "response": "one"},
            {"handled": True, "response": "two"},
        ])
        assert response is not None
        assert "остановлена" in response

    @pytest.mark.asyncio
    async def test_handled_message_returns_before_session_or_raw_id_logging(self, caplog):
        from gateway.run import GatewayRunner
        from gateway.session import Platform

        class Hooks:
            async def emit_collect(self, event_type, context):
                assert event_type == "ingress:message"
                assert context["media_urls"] == ["/private/cached.pdf"]
                return [{"handled": True, "response": "draft result"}]

        runner = GatewayRunner.__new__(GatewayRunner)
        object.__setattr__(runner, "hooks", Hooks())
        source = SimpleNamespace(
            platform=Platform.TELEGRAM,
            user_id="raw-user-123",
            chat_id="raw-chat-456",
            thread_id="raw-thread-789",
            chat_type="dm",
        )
        event = SimpleNamespace(
            text="",
            message_id="raw-message-321",
            message_type=SimpleNamespace(value="document"),
            media_urls=["/private/cached.pdf"],
            media_types=["application/pdf"],
        )

        result = await runner._handle_message_with_agent(event, source, "key", 1)

        assert result == "draft result"
        assert event._ingress_hook_handled is True
        logs = caplog.text
        assert "raw-user-123" not in logs
        assert "raw-chat-456" not in logs
        assert "raw-thread-789" not in logs
        assert "/private/cached.pdf" not in logs

    @pytest.mark.asyncio
    async def test_terminal_message_bypasses_session_and_agent_in_same_thread(self, caplog):
        from gateway.run import GatewayRunner
        from gateway.session import Platform

        class Hooks:
            async def emit_collect(self, event_type, context):
                return [{
                    "handled": True,
                    "status": "DUPLICATE_NO_WRITE",
                    "writes": 0,
                    "reply_mode": "SAME_THREAD",
                    "user_message": "Повторная запись не выполнялась.",
                }]

        class ForbiddenSessionStore:
            async def get_or_create_session(self, _source):
                raise AssertionError("terminal ingress must bypass session creation")

        runner = GatewayRunner.__new__(GatewayRunner)
        object.__setattr__(runner, "hooks", Hooks())
        object.__setattr__(runner, "_async_session_store", ForbiddenSessionStore())
        source = SimpleNamespace(
            platform=Platform.TELEGRAM,
            user_id="raw-user-terminal",
            chat_id="raw-chat-terminal",
            thread_id="48380",
            chat_type="dm",
        )
        event = SimpleNamespace(
            source=source,
            text="",
            message_id="raw-message-terminal",
            message_type=SimpleNamespace(value="document"),
            media_urls=["/private/terminal.pdf"],
            media_types=["application/pdf"],
        )

        result = await runner._handle_message_with_agent(event, source, "key", 1)

        assert result == "Повторная запись не выполнялась."
        assert event._ingress_hook_handled is True
        assert event.source is source
        assert event.source.thread_id == "48380"
        assert "raw-user-terminal" not in caplog.text
        assert "raw-chat-terminal" not in caplog.text
        assert "raw-message-terminal" not in caplog.text

    @pytest.mark.asyncio
    async def test_ingress_handler_exception_fails_closed_without_leaking_error(self, capsys):
        reg = HookRegistry()

        def _raises(_event_type, _context):
            raise RuntimeError("raw-user-should-not-be-logged")

        reg._handlers["ingress:message"] = [_raises]

        results = await reg.emit_collect("ingress:message", {})
        response = resolve_ingress_hook_decision(results)

        assert response is not None
        assert "остановлена" in response
        assert "raw-user-should-not-be-logged" not in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_ingress_dispatch_exception_bypasses_session_without_leaking_error(self, caplog):
        from gateway.run import GatewayRunner
        from gateway.session import Platform

        class Hooks:
            async def emit_collect(self, _event_type, _context):
                raise RuntimeError("raw-business-payload-should-not-be-logged")

        class ForbiddenSessionStore:
            async def get_or_create_session(self, _source):
                raise AssertionError("failed ingress dispatch must bypass session creation")

        runner = GatewayRunner.__new__(GatewayRunner)
        object.__setattr__(runner, "hooks", Hooks())
        object.__setattr__(runner, "_async_session_store", ForbiddenSessionStore())
        source = SimpleNamespace(
            platform=Platform.TELEGRAM,
            user_id="raw-user-dispatch",
            chat_id="raw-chat-dispatch",
            thread_id="48380",
            chat_type="dm",
        )
        event = SimpleNamespace(
            source=source,
            text="",
            message_id="raw-message-dispatch",
            message_type=SimpleNamespace(value="document"),
            media_urls=["/private/dispatch.pdf"],
            media_types=["application/pdf"],
        )

        result = await runner._handle_message_with_agent(event, source, "key", 1)

        assert "остановлена" in result
        assert event._ingress_hook_handled is True
        assert event.source.thread_id == "48380"
        assert "raw-business-payload-should-not-be-logged" not in caplog.text
        assert "raw-user-dispatch" not in caplog.text
        assert "/private/dispatch.pdf" not in caplog.text


def _patch_no_builtins(reg):
    """Suppress built-in hook registration so tests only exercise user-hook discovery."""
    return patch.object(reg, "_register_builtin_hooks")


class TestDiscoverAndLoad:
    def test_loads_valid_hook(self, tmp_path):
        _create_hook(tmp_path, "my-hook", '["agent:start"]',
                      "def handle(event_type, context):\n    pass\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path), _patch_no_builtins(reg):
            reg.discover_and_load()

        assert len(reg.loaded_hooks) == 1
        assert reg.loaded_hooks[0]["name"] == "my-hook"
        assert "agent:start" in reg.loaded_hooks[0]["events"]

    def test_skips_missing_hook_yaml(self, tmp_path):
        hook_dir = tmp_path / "bad-hook"
        hook_dir.mkdir()
        (hook_dir / "handler.py").write_text("def handle(e, c): pass\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path), _patch_no_builtins(reg):
            reg.discover_and_load()

        assert len(reg.loaded_hooks) == 0

    def test_skips_missing_handler_py(self, tmp_path):
        hook_dir = tmp_path / "bad-hook"
        hook_dir.mkdir()
        (hook_dir / "HOOK.yaml").write_text("name: bad\nevents: ['agent:start']\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path), _patch_no_builtins(reg):
            reg.discover_and_load()

        assert len(reg.loaded_hooks) == 0

    def test_skips_no_events(self, tmp_path):
        hook_dir = tmp_path / "empty-hook"
        hook_dir.mkdir()
        (hook_dir / "HOOK.yaml").write_text("name: empty\nevents: []\n")
        (hook_dir / "handler.py").write_text("def handle(e, c): pass\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path), _patch_no_builtins(reg):
            reg.discover_and_load()

        assert len(reg.loaded_hooks) == 0

    def test_skips_no_handle_function(self, tmp_path):
        hook_dir = tmp_path / "no-handle"
        hook_dir.mkdir()
        (hook_dir / "HOOK.yaml").write_text("name: no-handle\nevents: ['agent:start']\n")
        (hook_dir / "handler.py").write_text("def something_else(): pass\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path), _patch_no_builtins(reg):
            reg.discover_and_load()

        assert len(reg.loaded_hooks) == 0

    def test_nonexistent_hooks_dir(self, tmp_path):
        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path / "nonexistent"), _patch_no_builtins(reg):
            reg.discover_and_load()

        assert len(reg.loaded_hooks) == 0

    def test_multiple_hooks(self, tmp_path):
        _create_hook(tmp_path, "hook-a", '["agent:start"]',
                      "def handle(e, c): pass\n")
        _create_hook(tmp_path, "hook-b", '["session:start", "session:reset"]',
                      "def handle(e, c): pass\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path), _patch_no_builtins(reg):
            reg.discover_and_load()

        assert len(reg.loaded_hooks) == 2


class TestEmit:
    @pytest.mark.asyncio
    async def test_emit_calls_sync_handler(self, tmp_path):
        results = []

        _create_hook(tmp_path, "sync-hook", '["agent:start"]',
                      "results = []\n"
                      "def handle(event_type, context):\n"
                      "    results.append(event_type)\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path):
            reg.discover_and_load()

        # Inject our results list into the handler's module globals
        handler_fn = reg._handlers["agent:start"][0]
        handler_fn.__globals__["results"] = results

        await reg.emit("agent:start", {"test": True})
        assert "agent:start" in results

    @pytest.mark.asyncio
    async def test_emit_calls_async_handler(self, tmp_path):
        results = []

        hook_dir = tmp_path / "async-hook"
        hook_dir.mkdir()
        (hook_dir / "HOOK.yaml").write_text(
            "name: async-hook\nevents: ['agent:end']\n"
        )
        (hook_dir / "handler.py").write_text(
            "import asyncio\n"
            "results = []\n"
            "async def handle(event_type, context):\n"
            "    results.append(event_type)\n"
        )

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path):
            reg.discover_and_load()

        handler_fn = reg._handlers["agent:end"][0]
        handler_fn.__globals__["results"] = results

        await reg.emit("agent:end", {})
        assert "agent:end" in results

    @pytest.mark.asyncio
    async def test_wildcard_matching(self, tmp_path):
        results = []

        _create_hook(tmp_path, "wildcard-hook", '["command:*"]',
                      "results = []\n"
                      "def handle(event_type, context):\n"
                      "    results.append(event_type)\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path):
            reg.discover_and_load()

        handler_fn = reg._handlers["command:*"][0]
        handler_fn.__globals__["results"] = results

        await reg.emit("command:reset", {})
        assert "command:reset" in results

    @pytest.mark.asyncio
    async def test_no_handlers_for_event(self, tmp_path):
        reg = HookRegistry()
        # Should not raise and should have no handlers registered
        result = await reg.emit("unknown:event", {})
        assert result is None
        assert not reg._handlers.get("unknown:event")

    @pytest.mark.asyncio
    async def test_handler_error_does_not_propagate(self, tmp_path):
        _create_hook(tmp_path, "bad-hook", '["agent:start"]',
                      "def handle(event_type, context):\n"
                      "    raise ValueError('boom')\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path):
            reg.discover_and_load()

        assert len(reg._handlers.get("agent:start", [])) == 1
        # Should not raise even though handler throws
        result = await reg.emit("agent:start", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_emit_default_context(self, tmp_path):
        captured = []

        _create_hook(tmp_path, "ctx-hook", '["agent:start"]',
                      "captured = []\n"
                      "def handle(event_type, context):\n"
                      "    captured.append(context)\n")

        reg = HookRegistry()
        with patch("gateway.hooks.HOOKS_DIR", tmp_path):
            reg.discover_and_load()

        handler_fn = reg._handlers["agent:start"][0]
        handler_fn.__globals__["captured"] = captured

        await reg.emit("agent:start")  # no context arg
        assert captured[0] == {}


class TestEmitCollect:
    """Tests for emit_collect() — returns handler return values for decision-style hooks."""

    @pytest.mark.asyncio
    async def test_collects_sync_return_values(self):
        reg = HookRegistry()
        reg._handlers["command:status"] = [
            lambda _e, _c: {"decision": "allow"},
            lambda _e, _c: {"decision": "deny", "message": "nope"},
        ]

        results = await reg.emit_collect("command:status", {})

        assert results == [
            {"decision": "allow"},
            {"decision": "deny", "message": "nope"},
        ]

    @pytest.mark.asyncio
    async def test_collects_async_return_values(self):
        reg = HookRegistry()

        async def _async_handler(_event_type, _ctx):
            return {"decision": "handled", "message": "done"}

        reg._handlers["command:ping"] = [_async_handler]

        results = await reg.emit_collect("command:ping", {})

        assert results == [{"decision": "handled", "message": "done"}]

    @pytest.mark.asyncio
    async def test_drops_none_return_values(self):
        reg = HookRegistry()
        reg._handlers["command:x"] = [
            lambda _e, _c: None,  # fire-and-forget, returns nothing
            lambda _e, _c: {"decision": "deny"},
            lambda _e, _c: None,
        ]

        results = await reg.emit_collect("command:x", {})

        assert results == [{"decision": "deny"}]

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_abort_chain(self):
        reg = HookRegistry()

        def _raises(_e, _c):
            raise ValueError("boom")

        reg._handlers["command:x"] = [
            _raises,
            lambda _e, _c: {"decision": "allow"},
        ]

        results = await reg.emit_collect("command:x", {})

        # First handler's exception is swallowed; second handler's value still collected.
        assert results == [{"decision": "allow"}]

    @pytest.mark.asyncio
    async def test_wildcard_match_also_collected(self):
        reg = HookRegistry()
        reg._handlers["command:*"] = [lambda _e, _c: {"decision": "allow"}]
        reg._handlers["command:reset"] = [lambda _e, _c: {"decision": "deny"}]

        results = await reg.emit_collect("command:reset", {})

        # Exact match fires first, then wildcard.
        assert results == [{"decision": "deny"}, {"decision": "allow"}]

    @pytest.mark.asyncio
    async def test_no_handlers_returns_empty_list(self):
        reg = HookRegistry()

        results = await reg.emit_collect("unknown:event", {})

        assert results == []

    @pytest.mark.asyncio
    async def test_default_context(self):
        reg = HookRegistry()
        captured = []

        def _handler(event_type, context):
            captured.append((event_type, context))
            return None

        reg._handlers["agent:start"] = [_handler]

        await reg.emit_collect("agent:start")  # no context arg

        assert captured == [("agent:start", {})]
