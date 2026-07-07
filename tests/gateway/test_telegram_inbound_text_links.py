"""Tests for preserving inbound Telegram text_link entities for the agent."""

from types import SimpleNamespace

from plugins.platforms.telegram.adapter import TelegramAdapter


class TestTelegramHiddenTextLinks:
    def test_text_link_href_is_appended_for_agent_visibility(self):
        text = "Read Chapter 01 and Chapter 02"
        entities = [
            SimpleNamespace(type="text_link", offset=text.index("Chapter 01"), length=len("Chapter 01"), url="https://example.com/ch01.pdf"),
            SimpleNamespace(type="text_link", offset=text.index("Chapter 02"), length=len("Chapter 02"), url="https://example.com/ch02.pdf"),
        ]

        rendered = TelegramAdapter._render_text_with_hidden_entity_links(text, entities)

        assert rendered.startswith(text)
        assert "[Telegram hidden links]" in rendered
        assert "- Chapter 01: https://example.com/ch01.pdf" in rendered
        assert "- Chapter 02: https://example.com/ch02.pdf" in rendered

    def test_utf16_offsets_handle_astral_symbols_before_link(self):
        text = "😀 открыть главу"
        # Telegram offsets are UTF-16 code units; 😀 consumes two units.
        entities = [
            SimpleNamespace(type="MessageEntityType.TEXT_LINK", offset=3, length=7, url="https://example.com/chapter.pdf"),
        ]

        rendered = TelegramAdapter._render_text_with_hidden_entity_links(text, entities)

        assert "- открыть: https://example.com/chapter.pdf" in rendered

    def test_no_duplicate_when_url_already_visible(self):
        text = "https://example.com/ch01.pdf"
        entities = [
            SimpleNamespace(type="text_link", offset=0, length=len(text), url="https://example.com/ch01.pdf"),
        ]

        assert TelegramAdapter._render_text_with_hidden_entity_links(text, entities) == text
