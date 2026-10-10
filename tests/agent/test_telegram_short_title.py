"""Regression: Telegram policy reaches the real auxiliary generator and storage."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.title_generator import generate_title, maybe_auto_title, auto_title_session
from hermes_state import SessionDB

STYLE = "telegram-short-native-v1"


def response(title):
    import json
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"title": title})) )])


def test_short_prompt_not_soul():
    with patch("agent.title_generator.call_llm", return_value=response("CRM: переговоры")) as call:
        assert generate_title("Проверь CRM для ведения переговоров", title_style=STYLE) == "CRM: переговоры"
    prompt = call.call_args.kwargs["messages"][0]["content"]
    assert "1 to 4 words" in prompt
    assert "3 to 7 words" not in prompt
    assert "exact" in prompt


def test_oversized_output_is_rejected_not_stored():
    with patch("agent.title_generator.call_llm", return_value=response("Проверить crm prod frappe для ведения переговоров")):
        assert generate_title("Проверь CRM", title_style=STYLE) is None
        # Other surfaces keep their existing policy.
        assert generate_title("Проверь CRM") is not None


@pytest.mark.parametrize("opening, exact", [
    ('Создай топик с названием «G07 — тест 2026 CRM переговоры»', 'G07 — тест 2026 CRM переговоры'),
    ('Название темы: "Скилы"', 'Скилы'),
    ('Create a topic named "G01 Factory"', 'G01 Factory'),
])
def test_explicit_names_bypass_model_and_word_cap(opening, exact, tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="test-explicit", source="telegram")
    callbacks = []
    with patch("agent.title_generator.call_llm") as call:
        result = maybe_auto_title(db, "test-explicit", opening, title_style=STYLE,
                                 title_callback=lambda title, source: callbacks.append((title, source)))
    assert result is None
    call.assert_not_called()
    assert db.get_session_title("test-explicit") == exact
    assert db.get_session_title_source("test-explicit") == "llm"
    assert callbacks == [(exact, "llm")]
    db.close()


def test_invalid_explicit_name_does_not_fall_through_to_model(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="test-invalid", source="telegram")
    with patch("agent.title_generator.call_llm") as call:
        assert maybe_auto_title(db, "test-invalid", 'Название темы: "G01  exact"', title_style=STYLE) is None
    call.assert_not_called()
    assert db.get_session_title("test-invalid") is None
    db.close()


def test_manual_title_never_replaced(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="test-manual", source="telegram")
    db.set_session_title("test-manual", "G01 точное имя проекта 2026")
    with patch("agent.title_generator.call_llm") as call:
        auto_title_session(db, "test-manual", "Проверь CRM", title_style=STYLE)
    call.assert_not_called()
    assert db.get_session_title("test-manual") == "G01 точное имя проекта 2026"
    db.close()


def test_failure_fallback_stays_short(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="test-fallback", source="telegram")
    with patch("agent.title_generator.generate_title", return_value=None):
        auto_title_session(db, "test-fallback", "Проверить crm prod frappe для ведения переговоров", title_style=STYLE)
    assert len(db.get_session_title("test-fallback").split()) <= 4
    assert db.get_session_title_source("test-fallback") == "derived"
    db.close()
