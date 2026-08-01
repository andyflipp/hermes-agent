"""
Event Hook System

A lightweight event-driven system that fires handlers at key lifecycle points.
Hooks are discovered from ~/.hermes/hooks/ directories, each containing:
  - HOOK.yaml  (metadata: name, description, events list)
  - handler.py (Python handler with async def handle(event_type, context))

Events:
  - gateway:startup     -- Gateway process starts
  - ingress:message     -- Downloaded inbound message before the agent runs;
                           handlers may return a bounded handled response
  - session:start       -- New session created (first message of a new session)
  - session:end         -- Session ends (user ran /new or /reset)
  - session:reset       -- Session reset completed (new session entry created)
  - agent:start         -- Agent begins processing a message
  - agent:step          -- Each turn in the tool-calling loop
  - agent:end           -- Agent finishes processing
  - command:*           -- Any slash command executed (wildcard match)

Errors in notification hooks are caught and logged without blocking the main
pipeline. ``ingress:message`` is fail-closed because a handler may already
have performed an external side effect before returning.

Context dict passed to ``agent:start`` / ``agent:end`` handlers:
  platform     -- source platform name (e.g. "telegram", "matrix", "slack")
  user_id      -- platform user id of the sender
  chat_id      -- platform chat id (group/DM identifier)
  thread_id    -- Telegram forum-topic id / thread root id (string; empty
                  when not in a thread / topic)
  chat_type    -- "dm" | "group" | "forum" (empty if unknown)
  session_id   -- Hermes session id
  message      -- inbound message text (truncated to 500 chars)

``agent:end`` adds:
  response     -- agent response text (truncated to 500 chars)

Handlers posting a follow-up into the same Telegram forum-topic should
include ``message_thread_id=int(thread_id)`` when ``chat_type == "forum"``
and ``thread_id`` is non-empty.
"""

import asyncio
import importlib.util
import sys
from typing import Any, Callable, Dict, List, Optional

import yaml

from hermes_cli.config import get_hermes_home


HOOKS_DIR = get_hermes_home() / "hooks"

_INGRESS_TERMINAL_WRITES = {
    "CREATED_READBACK_PASS": 1,
    "DUPLICATE_NO_WRITE": 0,
    "EXCEPTION_NO_WRITE": 0,
    "UNCERTAIN_NO_RETRY": 0,
}
_INGRESS_TERMINAL_KEYS = {
    "handled", "status", "writes", "reply_mode", "user_message",
}
_INGRESS_FAILURE_RESPONSE = (
    "⚠️ Локальный обработчик не подтвердил безопасный результат. "
    "Обработка остановлена без запуска агента."
)


def _is_safe_terminal_user_message(message: str) -> bool:
    """Keep deterministic terminal replies text-only at the gateway boundary.

    Normal gateway responses are later scanned for native media directives.
    A document-derived ``user_message`` must therefore never be able to turn a
    terminal receipt into a local-file attachment request.
    """
    folded = message.casefold()
    return "\x00" not in message and "media:" not in folded and "[[" not in message


def resolve_ingress_hook_decision(results: List[Any]) -> Optional[str]:
    """Resolve at most one strict ``ingress:message`` handled response.

    A decision hook opts in with either the legacy exact response shape or the
    exact five-key terminal orchestration shape. Unknown unhandled values are
    ignored for notification-hook compatibility. Once a handler claims a
    message, malformed output and multiple claims fail closed rather than
    falling through to an LLM or choosing an order-dependent winner.
    """
    if type(results) is not list:
        return _INGRESS_FAILURE_RESPONSE

    decisions: List[str] = []
    malformed_claim = False
    for result in results:
        if not isinstance(result, dict):
            continue
        # A hostile dict subclass can make get()/iteration raise or lie. Hook
        # decisions cross a security boundary, so accept only a plain mapping.
        if type(result) is not dict:
            malformed_claim = True
            continue
        if result.get("handled") is not True:
            continue
        if set(result) == {"handled", "response"}:
            response = result.get("response")
            if isinstance(response, str) and response.strip():
                decisions.append(response.strip())
            else:
                malformed_claim = True
            continue
        if set(result) == _INGRESS_TERMINAL_KEYS:
            status = result.get("status")
            writes = result.get("writes")
            message = result.get("user_message")
            if (
                isinstance(status, str)
                and status in _INGRESS_TERMINAL_WRITES
                and type(writes) is int
                and writes == _INGRESS_TERMINAL_WRITES[status]
                and result.get("reply_mode") == "SAME_THREAD"
                and isinstance(message, str)
                and 0 < len(message.strip()) <= 256
                and _is_safe_terminal_user_message(message)
            ):
                decisions.append(message.strip())
            else:
                malformed_claim = True
            continue
        malformed_claim = True
    if malformed_claim:
        return _INGRESS_FAILURE_RESPONSE
    if not decisions:
        return None
    if len(decisions) > 1:
        return (
            "⚠️ Несколько локальных обработчиков заявили права на это сообщение. "
            "Обработка остановлена без запуска агента."
        )
    return decisions[0]


class HookRegistry:
    """
    Discovers, loads, and fires event hooks.

    Usage:
        registry = HookRegistry()
        registry.discover_and_load()
        await registry.emit("agent:start", {"platform": "telegram", ...})
    """

    def __init__(self):
        # event_type -> [handler_fn, ...]
        self._handlers: Dict[str, List[Callable]] = {}
        self._loaded_hooks: List[dict] = []  # metadata for listing

    @property
    def loaded_hooks(self) -> List[dict]:
        """Return metadata about all loaded hooks."""
        return list(self._loaded_hooks)

    def _register_builtin_hooks(self) -> None:
        """Register built-in hooks that are always active.

        Currently empty — no shipped built-in hooks. Kept as the extension
        point for future always-on gateway hooks so they drop in without
        re-plumbing discover_and_load().
        """
        return

    def discover_and_load(self) -> None:
        """
        Scan the hooks directory for hook directories and load their handlers.

        Also registers built-in hooks that are always active.

        Each hook directory must contain:
          - HOOK.yaml with at least 'name' and 'events' keys
          - handler.py with a top-level 'handle' function (sync or async)
        """
        self._register_builtin_hooks()

        if not HOOKS_DIR.exists():
            return

        for hook_dir in sorted(HOOKS_DIR.iterdir()):
            if not hook_dir.is_dir():
                continue

            manifest_path = hook_dir / "HOOK.yaml"
            handler_path = hook_dir / "handler.py"

            if not manifest_path.exists() or not handler_path.exists():
                continue

            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                if not manifest or not isinstance(manifest, dict):
                    print(f"[hooks] Skipping {hook_dir.name}: invalid HOOK.yaml", flush=True)
                    continue

                hook_name = manifest.get("name", hook_dir.name)
                events = manifest.get("events", [])
                if not events:
                    print(f"[hooks] Skipping {hook_name}: no events declared", flush=True)
                    continue

                # Dynamically load the handler module.
                # Register in sys.modules BEFORE exec_module so Pydantic /
                # dataclasses / typing introspection can resolve forward
                # references (triggered by `from __future__ import annotations`
                # in the handler). Without this, a handler that declares a
                # Pydantic BaseModel for webhook/event payloads fails at first
                # dispatch with "TypeAdapter ... is not fully defined".
                module_name = f"hermes_hook_{hook_name}"
                spec = importlib.util.spec_from_file_location(
                    module_name, handler_path
                )
                if spec is None or spec.loader is None:
                    print(f"[hooks] Skipping {hook_name}: could not load handler.py", flush=True)
                    continue

                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(module_name, None)
                    raise

                handle_fn = getattr(module, "handle", None)
                if handle_fn is None:
                    print(f"[hooks] Skipping {hook_name}: no 'handle' function found", flush=True)
                    continue

                # Register the handler for each declared event
                for event in events:
                    self._handlers.setdefault(event, []).append(handle_fn)

                self._loaded_hooks.append({
                    "name": hook_name,
                    "description": manifest.get("description", ""),
                    "events": events,
                    "path": str(hook_dir),
                })

                print(f"[hooks] Loaded hook '{hook_name}' for events: {events}", flush=True)

            except Exception as e:
                print(f"[hooks] Error loading hook {hook_dir.name}: {e}", flush=True)

    def _resolve_handlers(self, event_type: str) -> List[Callable]:
        """Return all handlers that should fire for ``event_type``.

        Exact matches fire first, followed by wildcard matches (e.g.
        ``command:*`` matches ``command:reset``).
        """
        handlers = list(self._handlers.get(event_type, []))
        if ":" in event_type:
            base = event_type.split(":")[0]
            wildcard_key = f"{base}:*"
            handlers.extend(self._handlers.get(wildcard_key, []))
        return handlers

    async def emit(self, event_type: str, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Fire all handlers registered for an event, discarding return values.

        Supports wildcard matching: handlers registered for "command:*" will
        fire for any "command:..." event. Handlers registered for a base type
        like "agent" won't fire for "agent:start" -- only exact matches and
        explicit wildcards.

        Args:
            event_type: The event identifier (e.g. "agent:start").
            context:    Optional dict with event-specific data.
        """
        if context is None:
            context = {}

        for fn in self._resolve_handlers(event_type):
            try:
                result = fn(event_type, context)
                # Support both sync and async handlers
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"[hooks] Error in handler for '{event_type}': {e}", flush=True)

    async def emit_collect(
        self,
        event_type: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        """Fire handlers and return their non-None return values in order.

        Like :meth:`emit` but captures each handler's return value. Used for
        decision-style hooks (e.g. ``command:<name>`` policies that want to
        allow/deny/rewrite the command before normal dispatch).

        Exceptions from individual handlers are logged but do not abort the
        remaining handlers.
        """
        if context is None:
            context = {}

        results: List[Any] = []
        for fn in self._resolve_handlers(event_type):
            try:
                result = fn(event_type, context)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    results.append(result)
            except Exception as e:
                if event_type == "ingress:message":
                    # Exception text may contain raw identifiers or payloads.
                    # Return a malformed handled sentinel so the ingress
                    # resolver fails closed without logging those details.
                    print("[hooks] Error in ingress handler (details suppressed)", flush=True)
                    results.append({"handled": True})
                else:
                    print(f"[hooks] Error in handler for '{event_type}': {e}", flush=True)
        return results
