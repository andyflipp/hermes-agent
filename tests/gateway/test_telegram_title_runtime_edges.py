"""Error and catalogue-isolation controls for the coordinated runtime patch."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.gateway.test_telegram_native_callback_regression import fire, lane
from plugins.platforms.telegram.adapter import TelegramAdapter
from gateway.config import Platform


@pytest.mark.asyncio
async def test_icon_resolver_exception_keeps_name_only_update(lane):
    lane.adapter.topic_icon_for_title = AsyncMock(side_effect=RuntimeError('synthetic resolver failure'))
    stored = await fire(lane, 'Скилы')
    lane.bot.edit_forum_topic.assert_awaited_once_with(
        chat_id=12345, message_thread_id=6789, name=stored,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('error_type', [TypeError, ValueError])
async def test_direct_bot_edit_failure_never_retries_unknown_write(lane, error_type):
    # Exercise the existing no-rename_dm_topic fallback, not the normal adapter.
    lane.bot.edit_forum_topic.side_effect = error_type('synthetic post-admission error')
    target = SimpleNamespace(_bot=lane.bot, topic_icon_for_title=lane.adapter.topic_icon_for_title)
    lane.runner._delivery_adapter_for = lambda source: target
    await fire(lane, 'Скилы')
    lane.bot.edit_forum_topic.assert_awaited_once()


@pytest.mark.asyncio
async def test_catalogues_remain_bound_to_receiving_adapter():
    def make(catalogue_id):
        adapter = object.__new__(TelegramAdapter)
        adapter.platform = Platform.TELEGRAM
        adapter._bot = SimpleNamespace(get_forum_topic_icon_stickers=AsyncMock(return_value=[
            SimpleNamespace(emoji='🔬', custom_emoji_id=catalogue_id),
        ]))
        return adapter

    a, b = make('900000000011'), make('900000000022')
    assert await a.topic_icon_for_title('Скилы') == '900000000011'
    assert await b.topic_icon_for_title('Скилы') == '900000000022'
    assert await a.topic_icon_for_title('Скилы') == '900000000011'
    a._bot.get_forum_topic_icon_stickers.assert_awaited_once()
    b._bot.get_forum_topic_icon_stickers.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_catalogue_refresh_uses_new_id(lane, monkeypatch):
    assert await lane.adapter.topic_icon_for_title('Скилы') == '900000000001'
    expires = lane.adapter._topic_icon_expiry
    lane.catalogue[0].custom_emoji_id = '900000000003'
    monkeypatch.setattr('plugins.platforms.telegram.adapter.time.monotonic', lambda: expires + 1)
    assert await lane.adapter.topic_icon_for_title('Скилы') == '900000000003'
    assert lane.bot.get_forum_topic_icon_stickers.await_count == 2
