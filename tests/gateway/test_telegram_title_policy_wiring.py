"""Policy crosses the actual gateway -> agent -> daemon-title boundaries."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.telegram_title_policy import TITLE_STYLE, requested_title
from agent.turn_context import _maybe_title_session_at_turn_start
from gateway.run_turn_runner import TurnRunner
from gateway.run_topics import GatewayTopicThreadsMixin
from gateway.config import Platform


@pytest.mark.parametrize("enabled", [False, True])
def test_gateway_attaches_style_only_with_transport_opt_in(enabled):
    source = SimpleNamespace(platform=Platform.TELEGRAM, chat_id="12345", thread_id="6789")
    calls = []
    runner = SimpleNamespace(
        config=SimpleNamespace(platforms={Platform.TELEGRAM: SimpleNamespace(extra={
            "topic_naming_policy": TITLE_STYLE if enabled else None,
        })}),
        _recover_discord_auto_thread_source=lambda src, key: src,
        _is_telegram_topic_lane=lambda src: True,
        _schedule_telegram_topic_title_rename=lambda src, sid, title: calls.append((src, sid, title)),
    )
    runner._telegram_topic_title_style = lambda src: GatewayTopicThreadsMixin._telegram_topic_title_style(runner, src)
    holder = SimpleNamespace(_runner=runner)
    agent = SimpleNamespace(session_id="fixture-session", platform="telegram", _session_db=object(),
                            _session_db_created=True, model="fixture-model", provider="fixture-provider")
    TurnRunner._attach_session_title_callback(holder, agent, SimpleNamespace(source=source, session_key="fixture-key"))
    assert agent._title_style == (TITLE_STYLE if enabled else None)
    with patch("agent.title_generator.maybe_auto_title", return_value=None) as dispatch:
        _maybe_title_session_at_turn_start(agent, [{"role": "user", "content": "Проверь CRM"}])
    assert dispatch.call_args.kwargs.get("title_style") == (TITLE_STYLE if enabled else None)
    agent._on_session_title("Opening placeholder", "derived")
    assert calls == []
    agent._on_session_title("CRM: переговоры", "llm")
    assert calls == [(source, "fixture-session", "CRM: переговоры")]


def test_quoted_document_title_is_not_chat_naming_authority():
    assert requested_title('Проверь документ с названием «Финансовый отчёт»') is None
    assert requested_title('Название книги: «Агенты»') is None


def test_explicit_literal_is_not_silently_normalized():
    with pytest.raises(ValueError):
        requested_title('Название темы: "G01  проект"')
    with pytest.raises(ValueError):
        requested_title('Название темы: " G01"')
