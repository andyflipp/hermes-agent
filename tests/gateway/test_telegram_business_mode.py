"""Telegram Business / Secretary mode gateway behavior."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageType, _thread_metadata_for_source
from gateway.session import SessionSource
from plugins.platforms.telegram.adapter import TelegramAdapter


class _FalseyNone:
    """Falsy sentinel that behaves like an absent Telegram attribute."""

    def __bool__(self):
        return False


def _make_adapter(extra=None) -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="fake-token", extra=extra or {}))
    adapter._bot = MagicMock()
    adapter._bot.id = 999
    adapter._bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
    adapter.handle_message = AsyncMock()
    return adapter


def _make_file_obj(data: bytes = b"hello"):
    file_obj = AsyncMock()
    file_obj.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    file_obj.file_path = "documents/file.txt"
    return file_obj


def _make_document(file_obj=None):
    doc = MagicMock()
    doc.file_name = "business.txt"
    doc.mime_type = "text/plain"
    doc.file_size = 32
    doc.get_file = AsyncMock(return_value=file_obj or _make_file_obj())
    return doc


def _make_message(*, business_connection_id=None, document=None, text="hello", from_is_bot=False):
    chat = SimpleNamespace(id=123, type="private", title=None, full_name="Business User", is_forum=False)
    user = SimpleNamespace(id=456, full_name="Business User", username="business_user", is_bot=from_is_bot)
    return SimpleNamespace(
        message_id=77,
        text=text,
        caption=None,
        date=None,
        chat=chat,
        from_user=user,
        sender_chat=None,
        message_thread_id=None,
        is_topic_message=False,
        reply_to_message=None,
        quote=None,
        forum_topic_created=None,
        business_connection_id=business_connection_id,
        photo=None,
        video=None,
        audio=None,
        voice=None,
        sticker=None,
        document=document,
        media_group_id=None,
        location=None,
        venue=None,
        entities=None,
    )


def _make_update(*, message=None, business_message=None):
    effective = business_message or message
    return SimpleNamespace(
        update_id=1001,
        message=message,
        business_message=business_message,
        effective_message=effective,
        business_connection=None,
        deleted_business_messages=None,
    )


def test_session_source_round_trips_telegram_business_fields():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        user_id="456",
        telegram_business_connection_id="bc_123",
        telegram_business_user_chat_id="777",
        telegram_business_user_id="456",
    )

    restored = SessionSource.from_dict(source.to_dict())

    assert restored.telegram_business_connection_id == "bc_123"
    assert restored.telegram_business_user_chat_id == "777"
    assert restored.telegram_business_user_id == "456"


def test_thread_metadata_includes_business_connection_without_thread():
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        user_id="456",
        telegram_business_connection_id="bc_123",
        telegram_business_user_chat_id="777",
        telegram_business_user_id="456",
    )

    metadata = _thread_metadata_for_source(source)

    assert metadata["business_connection_id"] == "bc_123"
    assert metadata["telegram_business_connection_id"] == "bc_123"
    assert metadata["telegram_business_user_chat_id"] == "777"
    assert metadata["telegram_business_user_id"] == "456"


def test_build_message_event_preserves_business_connection_id():
    adapter = _make_adapter()
    msg = _make_message(business_connection_id="bc_123")

    event = adapter._build_message_event(msg, MessageType.TEXT, update_id=1001)

    assert event.source.telegram_business_connection_id == "bc_123"
    assert event.source.telegram_business_user_chat_id == "123"
    assert event.source.telegram_business_user_id == "456"


@pytest.mark.asyncio
async def test_business_media_uses_effective_message(tmp_path, monkeypatch):
    adapter = _make_adapter(
        {
            "business_mode": {
                "enabled": True,
                "reply_enabled": True,
                "allow_business_connection_ids": ["bc_123"],
            }
        }
    )
    adapter._is_callback_user_authorized = lambda *_args, **_kwargs: True
    monkeypatch.setattr("gateway.platforms.base.DOCUMENT_CACHE_DIR", tmp_path / "docs")
    msg = _make_message(
        business_connection_id="bc_123",
        document=_make_document(_make_file_obj(b"business body")),
        text="",
    )
    update = _make_update(message=None, business_message=msg)

    await adapter._handle_media_message(update, MagicMock())

    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.call_args.args[0]
    assert event.source.telegram_business_connection_id == "bc_123"
    assert event.message_type == MessageType.DOCUMENT


@pytest.mark.asyncio
async def test_business_message_observed_but_not_dispatched_when_reply_disabled(caplog):
    adapter = _make_adapter({"business_mode": {"enabled": True, "reply_enabled": False}})
    msg = _make_message(business_connection_id="bc_123")
    update = _make_update(message=None, business_message=msg)

    with caplog.at_level(logging.WARNING):
        await adapter._handle_text_message(update, MagicMock())

    adapter.handle_message.assert_not_awaited()
    assert "Observed Business/Secretary message with replies disabled" in caplog.text
    assert "bc_123" in caplog.text


@pytest.mark.asyncio
async def test_send_message_passes_business_connection_id_when_enabled_and_allowed():
    adapter = _make_adapter(
        {
            "business_mode": {
                "enabled": True,
                "reply_enabled": True,
                "allow_business_connection_ids": ["bc_123"],
            }
        }
    )

    result = await adapter.send(
        "123",
        "hello",
        metadata={"business_connection_id": "bc_123", "telegram_business_chat_id": "123"},
    )

    assert result.success is True
    adapter._bot.send_message.assert_awaited_once()
    assert adapter._bot.send_message.await_args.kwargs["business_connection_id"] == "bc_123"


@pytest.mark.asyncio
async def test_send_message_refuses_business_connection_until_reply_enabled():
    adapter = _make_adapter({"business_mode": {"enabled": True, "reply_enabled": False}})

    result = await adapter.send(
        "123",
        "hello",
        metadata={"business_connection_id": "bc_123"},
    )

    assert result.success is False
    assert result.error == "telegram_business_reply_disabled"
    adapter._bot.send_message.assert_not_awaited()


def test_bot_sender_ignored_in_business_mode_by_default():
    adapter = _make_adapter({"business_mode": {"enabled": True, "reply_enabled": True}})
    msg = _make_message(business_connection_id="bc_123", from_is_bot=True)

    assert adapter._should_process_message(msg) is False


@pytest.mark.asyncio
async def test_business_connection_handler_records_state():
    adapter = _make_adapter({"business_mode": {"enabled": True}})
    business_connection = SimpleNamespace(
        id="bc_123",
        user=SimpleNamespace(id=456, full_name="Business User"),
        user_chat_id=123,
        date=None,
        is_enabled=True,
        rights=SimpleNamespace(can_reply=True),
    )
    update = SimpleNamespace(business_connection=business_connection)

    await adapter._handle_business_connection(update, MagicMock())

    assert adapter._business_connections["bc_123"]["user_chat_id"] == "123"
    assert adapter._business_connections["bc_123"]["user_id"] == "456"
    assert adapter._business_connections["bc_123"]["is_enabled"] is True


@pytest.mark.asyncio
async def test_send_typing_passes_business_connection_id_when_allowed():
    adapter = _make_adapter(
        {
            "business_mode": {
                "enabled": True,
                "reply_enabled": True,
                "allow_business_connection_ids": ["bc_123"],
            }
        }
    )
    adapter._bot.send_chat_action = AsyncMock()

    await adapter.send_typing("123", metadata={"business_connection_id": "bc_123"})

    adapter._bot.send_chat_action.assert_awaited_once()
    assert adapter._bot.send_chat_action.await_args.kwargs["business_connection_id"] == "bc_123"


@pytest.mark.asyncio
async def test_send_typing_skips_business_connection_until_reply_enabled():
    adapter = _make_adapter({"business_mode": {"enabled": True, "reply_enabled": False}})
    adapter._bot.send_chat_action = AsyncMock()

    await adapter.send_typing("123", metadata={"business_connection_id": "bc_123"})

    adapter._bot.send_chat_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_document_passes_business_connection_id_when_allowed(tmp_path):
    adapter = _make_adapter(
        {
            "business_mode": {
                "enabled": True,
                "reply_enabled": True,
                "allow_business_connection_ids": ["bc_123"],
            }
        }
    )
    adapter._bot.send_document = AsyncMock(return_value=SimpleNamespace(message_id=99))
    path = tmp_path / "business.txt"
    path.write_text("business doc", encoding="utf-8")

    result = await adapter.send_document(
        "123",
        str(path),
        metadata={"business_connection_id": "bc_123"},
    )

    assert result.success is True
    adapter._bot.send_document.assert_awaited_once()
    assert adapter._bot.send_document.await_args.kwargs["business_connection_id"] == "bc_123"
