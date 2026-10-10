"""Telegram short-title contract exercised through the installed turn-start API.

No proposed title_style keyword or policy-module import: these tests collect on
44a1ce and fail on observable output, not missing implementation symbols.
The opt-in name follows the pre-existing candidate; 1–4 words and literal names
come from the owner's telegram-topic-naming:v1 rule. Non-Telegram behavior stays.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.turn_context import _maybe_title_session_at_turn_start
from gateway.config import Platform
from gateway.run_turn_runner import TurnRunner
from hermes_state import SessionDB

STYLE = "telegram-short-native-v1"
OPENING = "Проверить crm prod frappe для ведения переговоров"
LONG_TITLE = "Проверить crm prod frappe для ведения переговоров"


def response(title):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=json.dumps({"title": title})))])


@pytest.fixture
def session(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="synthetic-topic", source="telegram")
    agent = SimpleNamespace(
        _session_db=db, session_id="synthetic-topic", _session_db_created=True,
        platform="telegram", model="synthetic-model", provider="synthetic-provider",
        _title_style=STYLE,
    )
    yield db, agent
    db.close()


def run_title(agent, opening, returned_title="CRM: переговоры", upgrade=True):
    callbacks = []
    agent._on_session_title = lambda title, source: callbacks.append((title, source))
    with patch("agent.title_generator._auto_title_enabled", return_value=True), \
         patch("agent.title_generator._model_title_upgrade_enabled", return_value=upgrade), \
         patch("agent.title_generator._title_language", return_value=""), \
         patch("agent.title_generator.call_llm", return_value=response(returned_title)) as llm, \
         patch("agent.title_generator.title_upgrade_must_wait_for_turn", return_value=True):
        _maybe_title_session_at_turn_start(agent, [{"role": "user", "content": opening}])
        thread = getattr(agent, "_deferred_title_upgrade", None)
        if thread is not None:
            thread.start()
            thread.join(10)
            assert not thread.is_alive(), "Synthetic titler did not finish"
        return llm, callbacks


def test_telegram_turn_uses_short_auxiliary_prompt(session):
    db, agent = session
    llm, _ = run_title(agent, OPENING)
    assert db.get_session_title(agent.session_id) == "CRM: переговоры"
    prompt = llm.call_args.kwargs["messages"][0]["content"]
    assert "1 to 4 words" in prompt
    assert "3 to 7 words" not in prompt


def test_telegram_oversized_model_title_not_persisted(session):
    db, agent = session
    _, callbacks = run_title(agent, OPENING, returned_title=LONG_TITLE)
    actual = db.get_session_title(agent.session_id)
    assert len(actual.split()) <= 4, repr(actual)
    assert db.get_session_title_source(agent.session_id) == "derived"
    assert all(title != LONG_TITLE for title, source in callbacks if source == "llm")


def test_telegram_model_failure_keeps_short_provisional_title(session):
    db, agent = session
    run_title(agent, OPENING, returned_title="")
    actual = db.get_session_title(agent.session_id)
    assert len(actual.split()) <= 4, repr(actual)
    assert db.get_session_title_source(agent.session_id) == "derived"


def test_telegram_disabled_upgrade_keeps_short_instant_title(session):
    db, agent = session
    llm, _ = run_title(agent, OPENING, upgrade=False)
    llm.assert_not_called()
    actual = db.get_session_title(agent.session_id)
    assert len(actual.split()) <= 4, repr(actual)


def test_explicit_topic_name_is_literal_and_bypasses_auxiliary(session):
    db, agent = session
    exact = "G07 — тест 2026 CRM переговоры"
    llm, callbacks = run_title(agent, f"Создай топик с названием «{exact}»")
    assert db.get_session_title(agent.session_id) == exact
    llm.assert_not_called()
    assert callbacks == [(exact, "llm")]


def test_manual_title_retains_authority(session):
    db, agent = session
    exact = "G01 точное имя проекта 2026"
    db.set_session_title(agent.session_id, exact)
    llm, _ = run_title(agent, OPENING)
    llm.assert_not_called()
    assert db.get_session_title(agent.session_id) == exact
    assert db.get_session_title_source(agent.session_id) == "user"


def test_nontelegram_keeps_existing_title_policy(session):
    db, agent = session
    agent.platform = "cli"
    del agent._title_style
    llm, _ = run_title(agent, OPENING, returned_title=LONG_TITLE)
    assert db.get_session_title(agent.session_id) == LONG_TITLE
    assert "3 to 7 words" in llm.call_args.kwargs["messages"][0]["content"]


def test_gateway_opt_in_reaches_actual_turn_start_dispatch():
    source = SimpleNamespace(platform=Platform.TELEGRAM, chat_id="synthetic-chat", thread_id="123")
    config = SimpleNamespace(extra={"topic_naming_policy": STYLE})
    runner = SimpleNamespace(
        config=SimpleNamespace(platforms={Platform.TELEGRAM: config}),
        _recover_discord_auto_thread_source=lambda src, key: src,
        _is_telegram_topic_lane=lambda src: True,
        _telegram_topic_title_style=lambda src: STYLE,
        _schedule_telegram_topic_title_rename=lambda *args: None,
    )
    agent = SimpleNamespace(session_id="synthetic-topic", platform="telegram",
                            _session_db=object(), _session_db_created=True,
                            model="synthetic-model", provider="synthetic-provider")
    TurnRunner._attach_session_title_callback(SimpleNamespace(_runner=runner), agent,
                                             SimpleNamespace(source=source, session_key="synthetic-key"))
    with patch("agent.title_generator.maybe_auto_title", return_value=None) as dispatch:
        _maybe_title_session_at_turn_start(agent, [{"role": "user", "content": OPENING}])
    assert dispatch.call_args.kwargs.get("title_style") == STYLE
