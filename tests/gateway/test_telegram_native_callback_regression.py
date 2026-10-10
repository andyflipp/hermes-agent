"""Owner-contract RED via existing callback/API, not missing proposed symbols.

No Telegram transport is constructed. IDs/catalogues are synthetic per-test;
all outbound Bot API calls are AsyncMocks and the runner forbids network.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.run_turn_runner import TurnRunner
from gateway.session import SessionSource
from hermes_state import SessionDB
from plugins.platforms.telegram.adapter import TelegramAdapter


@pytest.fixture
def lane(tmp_path, monkeypatch):
    import gateway.run as gateway_run

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("synthetic-session", source="telegram", user_id="12345")
    catalogue = [SimpleNamespace(emoji="🔬", custom_emoji_id="900000000001")]
    bot = SimpleNamespace(
        get_forum_topic_icon_stickers=AsyncMock(return_value=catalogue),
        edit_forum_topic=AsyncMock(return_value=True),
    )
    adapter = object.__new__(TelegramAdapter)
    adapter.platform = Platform.TELEGRAM
    adapter._bot = bot
    adapter._dm_topics = {}
    adapter._dm_topics_config = []
    # Cache refresh is config I/O, not the path under test.
    adapter._reload_dm_topics_from_config = lambda: None
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(
        enabled=True, extra={"topic_naming_policy": "telegram-short-native-v1"},
    )})
    runner._delivery_adapter_for = lambda source: adapter
    runner._telegram_topic_mode_enabled = lambda source: True
    runner._session_db = SimpleNamespace(get_telegram_topic_binding=AsyncMock(
        return_value={"session_id": "synthetic-session"},
    ))
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="12345",
                           user_id="12345", thread_id="6789", chat_type="dm")
    scheduled = []
    real_schedule = gateway_run.safe_schedule_threadsafe

    def schedule(coro, loop, **kwargs):
        future = real_schedule(coro, loop, **kwargs)
        scheduled.append(future)
        return future

    monkeypatch.setattr(gateway_run, "safe_schedule_threadsafe", schedule)
    value = SimpleNamespace(db=db, bot=bot, adapter=adapter, runner=runner,
                            source=source, scheduled=scheduled, catalogue=catalogue)
    yield value
    db.close()


async def fire(lane, title, provenance="llm"):
    """Read stored title, register real TurnRunner callback, fire from a thread."""
    lane.db.set_session_title("synthetic-session", title)
    stored = lane.db.get_session_title("synthetic-session")
    agent = SimpleNamespace(session_id="synthetic-session")
    lane.runner._gateway_loop = asyncio.get_running_loop()
    TurnRunner._attach_session_title_callback(
        SimpleNamespace(_runner=lane.runner), agent,
        SimpleNamespace(source=lane.source, session_key="synthetic-key"),
    )
    assert callable(agent._on_session_title)
    await asyncio.to_thread(agent._on_session_title, stored, provenance)
    for future in lane.scheduled:
        if future is not None:
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=5)
    return stored


@pytest.mark.asyncio
@pytest.mark.parametrize("catalogue_id", ["900000000001", "900000000002"])
async def test_registered_callback_uses_receiving_bots_native_catalogue(lane, catalogue_id):
    lane.catalogue[0].custom_emoji_id = catalogue_id
    stored = await fire(lane, "Скилы")
    lane.bot.edit_forum_topic.assert_awaited_once()
    payload = lane.bot.edit_forum_topic.await_args.kwargs
    assert (payload["chat_id"], payload["message_thread_id"], payload["name"]) == (12345, 6789, stored)
    assert payload.get("icon_custom_emoji_id") == catalogue_id
    lane.bot.get_forum_topic_icon_stickers.assert_awaited_once()
    assert "🔬" not in payload["name"], "native icon is not decoration in the title"


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["G07 — тест 2026 CRM переговоры", "G07 — " + "я" * 94])
async def test_callback_keeps_storage_valid_literal_title_identical_to_session(lane, title):
    # SessionDB allows <=100 chars; Bot API allows <=128. Preserve both contracts.
    stored = await fire(lane, title)
    lane.bot.edit_forum_topic.assert_awaited_once()
    assert lane.bot.edit_forum_topic.await_args.kwargs["name"] == stored


@pytest.mark.asyncio
async def test_disabled_opt_in_preserves_legacy_name_only_api(lane):
    lane.runner.config.platforms[Platform.TELEGRAM].extra.pop("topic_naming_policy")
    stored = await fire(lane, "G07: переговоры")
    lane.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=12345, message_thread_id=6789, name=stored,
    )
    lane.bot.get_forum_topic_icon_stickers.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["stale-binding", "binding-error", "kill-switch", "operator-topic", "group"])
async def test_existing_authority_gates_prevent_any_api_effect(lane, gate):
    if gate == "stale-binding":
        lane.runner._session_db.get_telegram_topic_binding.return_value = {"session_id": "different-session"}
    elif gate == "binding-error":
        lane.runner._session_db.get_telegram_topic_binding.side_effect = RuntimeError("synthetic DB failure")
    elif gate == "kill-switch":
        lane.runner.config.platforms[Platform.TELEGRAM].extra["disable_topic_auto_rename"] = True
    elif gate == "operator-topic":
        lane.adapter._dm_topics = {"12345:Operator topic": 6789}
        lane.adapter._dm_topics_config = [{"chat_id": "12345", "topics": [{"name": "Operator topic"}]}]
    else:
        lane.source.chat_type = "group"
        # A group has no title callback in this existing DM-only lane.
        await lane.runner._rename_telegram_topic_for_session_title(lane.source, "synthetic-session", "Скилы")
        lane.bot.edit_forum_topic.assert_not_awaited()
        lane.bot.get_forum_topic_icon_stickers.assert_not_awaited()
        return
    await fire(lane, "Скилы")
    lane.bot.edit_forum_topic.assert_not_awaited()
    lane.bot.get_forum_topic_icon_stickers.assert_not_awaited()


@pytest.mark.asyncio
async def test_derived_stage_spends_no_topic_api_call(lane):
    await fire(lane, "Скилы", provenance="derived")
    lane.bot.edit_forum_topic.assert_not_awaited()
    lane.bot.get_forum_topic_icon_stickers.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_edit_failure_is_suppressed_without_duplicate_write(lane):
    lane.bot.edit_forum_topic.side_effect = RuntimeError("synthetic transport failure")
    await fire(lane, "Скилы")
    lane.bot.edit_forum_topic.assert_awaited_once()
    assert lane.db.get_session_title("synthetic-session") == "Скилы"


@pytest.mark.asyncio
async def test_catalogue_failure_does_not_invent_id_or_decorate_title(lane):
    lane.bot.get_forum_topic_icon_stickers.side_effect = RuntimeError("synthetic catalogue failure")
    stored = await fire(lane, "Скилы")
    lane.bot.get_forum_topic_icon_stickers.assert_awaited_once()
    lane.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=12345, message_thread_id=6789, name=stored,
    )


@pytest.mark.asyncio
async def test_empty_catalogue_does_not_send_an_icon_clear_operation(lane):
    lane.bot.get_forum_topic_icon_stickers.return_value = []
    stored = await fire(lane, "Скилы")
    lane.bot.get_forum_topic_icon_stickers.assert_awaited_once()
    # Empty string would REMOVE the existing icon, not preserve it.
    lane.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=12345, message_thread_id=6789, name=stored,
    )
