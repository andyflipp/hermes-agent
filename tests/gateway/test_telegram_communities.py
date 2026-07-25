"""Telegram Bot API 10.2 Community compatibility tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from plugins.platforms.telegram.adapter import ChatType, TelegramAdapter


def _adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token", extra={}))
    bot = MagicMock()
    bot.username = "hermes_test_bot"
    adapter._bot = bot
    return adapter


def _message(*, api_kwargs=None, text="hello", message_id=10):
    user = SimpleNamespace(id=42, full_name="Alice", is_bot=False)
    chat = SimpleNamespace(
        id=-100123,
        type=ChatType.SUPERGROUP,
        title="Test group",
        full_name=None,
        is_forum=False,
        username=None,
    )
    return SimpleNamespace(
        api_kwargs=api_kwargs or {},
        text=text,
        caption=None,
        entities=None,
        message_id=message_id,
        message_thread_id=None,
        is_topic_message=False,
        reply_to_message=None,
        quote=None,
        date=None,
        from_user=user,
        chat=chat,
    )


def test_community_added_metadata_uses_exact_string_id_and_updates_cache():
    adapter = _adapter()
    message = _message(
        api_kwargs={
            "community_chat_added": {
                "community": {"id": 9007199254740991, "name": "Voron"}
            }
        }
    )

    metadata = adapter._community_metadata_for_message(message)

    assert metadata == {
        "telegram_community_id": "9007199254740991",
        "telegram_community_name": "Voron",
        "telegram_community_event": "added",
    }
    assert adapter._telegram_communities_by_chat["-100123"] == {
        "telegram_community_id": "9007199254740991",
        "telegram_community_name": "Voron",
    }


def test_cached_community_is_added_to_normal_message_event_without_changing_source_identity():
    adapter = _adapter()
    adapter._telegram_communities_by_chat["-100123"] = {
        "telegram_community_id": "777",
        "telegram_community_name": "Graph OS",
    }

    event = adapter._build_message_event(_message(), MessageType.TEXT)

    assert event.metadata["telegram_community_id"] == "777"
    assert event.metadata["telegram_community_name"] == "Graph OS"
    assert "telegram_community_event" not in event.metadata
    assert event.source.chat_id == "-100123"
    assert event.source.thread_id is None
    assert event.source.delivery_metadata == {}


def test_community_removed_event_reports_last_known_identity_and_clears_cache():
    adapter = _adapter()
    adapter._telegram_communities_by_chat["-100123"] = {
        "telegram_community_id": "777",
        "telegram_community_name": "Graph OS",
    }
    message = _message(api_kwargs={"community_chat_removed": {}})

    metadata = adapter._community_metadata_for_message(message)

    assert metadata == {
        "telegram_community_id": "777",
        "telegram_community_name": "Graph OS",
        "telegram_community_event": "removed",
    }
    assert "-100123" not in adapter._telegram_communities_by_chat


@pytest.mark.asyncio
async def test_get_chat_info_parses_community_from_ptb_api_kwargs():
    adapter = _adapter()
    chat = SimpleNamespace(
        id=-100123,
        type=ChatType.SUPERGROUP,
        title="Support",
        full_name=None,
        username=None,
        is_forum=False,
        api_kwargs={"community": {"id": 888, "name": "Voron Clients"}},
    )
    adapter._bot.get_chat = AsyncMock(return_value=chat)

    info = await adapter.get_chat_info("-100123")

    assert info["community_id"] == "888"
    assert info["community_name"] == "Voron Clients"
    assert adapter._telegram_communities_by_chat["-100123"] == {
        "telegram_community_id": "888",
        "telegram_community_name": "Voron Clients",
    }


@pytest.mark.asyncio
async def test_get_chat_info_without_community_clears_stale_cache():
    adapter = _adapter()
    adapter._telegram_communities_by_chat["-100123"] = {
        "telegram_community_id": "777",
        "telegram_community_name": "Stale Community",
    }
    chat = SimpleNamespace(
        id=-100123,
        type=ChatType.SUPERGROUP,
        title="Support",
        full_name=None,
        username=None,
        is_forum=False,
        api_kwargs={},
    )
    adapter._bot.get_chat = AsyncMock(return_value=chat)

    info = await adapter.get_chat_info("-100123")

    assert "community_id" not in info
    assert "community_name" not in info
    assert "-100123" not in adapter._telegram_communities_by_chat


@pytest.mark.asyncio
async def test_community_service_handler_updates_cache_without_agent_dispatch():
    adapter = _adapter()
    adapter.handle_message = AsyncMock()
    message = _message(
        text=None,
        api_kwargs={
            "community_chat_added": {
                "community": {"id": 999, "name": "Operations"}
            }
        },
    )
    update = SimpleNamespace(effective_message=message, message=message)

    await adapter._handle_community_service_message(update, None)

    assert adapter._telegram_communities_by_chat["-100123"][
        "telegram_community_id"
    ] == "999"
    adapter.handle_message.assert_not_awaited()
