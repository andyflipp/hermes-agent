"""Native icons must come from the bot's actual sticker catalogue, not fixed IDs."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from plugins.platforms.telegram.adapter import TelegramAdapter
from gateway.run_topics import GatewayTopicThreadsMixin
from gateway.config import Platform
from gateway.session import SessionSource


def adapter(bot):
    obj = object.__new__(TelegramAdapter)
    obj._bot = bot
    obj.platform = Platform.TELEGRAM
    return obj


@pytest.mark.asyncio
async def test_rename_passes_native_icon_and_exact_name():
    bot = SimpleNamespace(edit_forum_topic=AsyncMock())
    await adapter(bot).rename_dm_topic("12345", "6789", "CRM: переговоры", icon_custom_emoji_id="123")
    bot.edit_forum_topic.assert_awaited_once_with(chat_id=12345, message_thread_id=6789,
                                                name="CRM: переговоры", icon_custom_emoji_id="123")


@pytest.mark.asyncio
async def test_name_only_remains_backwards_compatible():
    bot = SimpleNamespace(edit_forum_topic=AsyncMock())
    await adapter(bot).rename_dm_topic("12345", "6789", "G01 exact")
    bot.edit_forum_topic.assert_awaited_once_with(chat_id=12345, message_thread_id=6789, name="G01 exact")


@pytest.mark.asyncio
async def test_live_catalogue_resolution_and_cache():
    bot = SimpleNamespace(get_forum_topic_icon_stickers=AsyncMock(return_value=[
        SimpleNamespace(emoji="🔬", custom_emoji_id="microscope-valid"),
        SimpleNamespace(emoji="💬", custom_emoji_id="chat-valid"),
        SimpleNamespace(emoji="📝", custom_emoji_id="notes-valid"),
    ]))
    obj = adapter(bot)
    assert await obj.topic_icon_for_title("Скилы") == "microscope-valid"
    assert await obj.topic_icon_for_title("CRM: переговоры") == "chat-valid"
    assert await obj.topic_icon_for_title("Неизвестный предмет") == "notes-valid"
    assert bot.get_forum_topic_icon_stickers.await_count == 1


@pytest.mark.asyncio
async def test_catalogue_error_never_invents_an_id():
    bot = SimpleNamespace(get_forum_topic_icon_stickers=AsyncMock(side_effect=RuntimeError("offline")))
    assert await adapter(bot).topic_icon_for_title("Скилы") is None


def runner(stale=False, disabled=False):
    obj = object.__new__(GatewayTopicThreadsMixin)
    obj.config = SimpleNamespace(platforms={Platform.TELEGRAM: SimpleNamespace(extra={
        "topic_naming_policy": "telegram-short-native-v1", "disable_topic_auto_rename": disabled,
    })})
    target = SimpleNamespace(rename_dm_topic=AsyncMock(), topic_icon_for_title=AsyncMock(return_value="valid-native-id"))
    obj._delivery_adapter_for = lambda source: target
    obj._is_telegram_topic_lane = lambda source: True
    obj._session_db = SimpleNamespace(get_telegram_topic_binding=AsyncMock(return_value={
        "session_id": "other" if stale else "session-1",
    }))
    return obj, target


@pytest.mark.asyncio
async def test_real_gateway_callback_keeps_db_title_and_origin():
    obj, target = runner()
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="12345", thread_id="6789", profile="default")
    await obj._rename_telegram_topic_for_session_title(source, "session-1", "CRM: переговоры")
    target.rename_dm_topic.assert_awaited_once_with(chat_id="12345", thread_id="6789",
                                                   name="CRM: переговоры", icon_custom_emoji_id="valid-native-id")


@pytest.mark.asyncio
@pytest.mark.parametrize("stale,disabled", [(True,False),(False,True)])
async def test_stale_binding_and_kill_switch_no_external_write(stale, disabled):
    obj, target = runner(stale, disabled)
    source = SessionSource(platform=Platform.TELEGRAM, chat_id="12345", thread_id="6789", profile="default")
    await obj._rename_telegram_topic_for_session_title(source, "session-1", "CRM: переговоры")
    target.rename_dm_topic.assert_not_awaited()
