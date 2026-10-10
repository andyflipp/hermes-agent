"""Opt-in Telegram title/icon policy. No network, credentials or fixed emoji IDs."""
import re

TITLE_STYLE = "telegram-short-native-v1"
MAX_WORDS = 4
SHORT_PROMPT = (
    "You name Telegram topics and their matching chat sessions.\n"
    "Rules:\n"
    "- 1 to 4 words. Prefer a concise subject label, not an instruction or a sentence.\n"
    "- Preserve explicit names, project codes, identifiers and numbers exact.\n"
    "- No emoji in the title: the Telegram native icon is selected separately.\n"
    "- Do not answer the message. No prefix, quotes or trailing punctuation.\n"
    "- A bare greeting must be named Friendly greeting (a provisional placeholder).\n"
    "__LANGUAGE_RULE__\n"
    'Reply with JSON only: {"title": "..."}'
)

# Recognize only an explicit TOPIC/session naming request; quoted filenames or
# document titles are not permission to rename this chat.
_EXPLICIT = re.compile(
    r'(?:\bназвание\s+(?:темы|топика|сессии)\s*[:=]?|'
    r'\b(?:тема|топик|сессия)\s+(?:с\s+)?(?:названием|под\s+названием)|'
    r'\b(?:topic|session)\s+(?:named|called|title\s*[:=]))\s*'
    r'(?:«([^»]+)»|"([^"]+)")', re.IGNORECASE,
)


def requested_title(message):
    match = _EXPLICIT.search(message or "")
    if not match:
        return None
    title = next(group for group in match.groups() if group is not None)
    # Do not silently normalize explicit values. The normal title store enforces
    # its control-char/length contract; unsupported literals are not auto-applied.
    if title != title.strip() or re.search(r'[\x00-\x1f]|\s{2,}', title) or len(title) > 128:
        raise ValueError("Explicit topic title violates the literal title contract")
    return title


def short_fallback(title):
    return " ".join(title.split()[:MAX_WORDS]) if title else None


_ICON_RULES = (
    (r'скил|навык|skill|исслед|research|лаборат|science', ("🔬", "🔍", "📝")),
    (r'переговор|negotiat|crm|клиент|client', ("🤝", "💬", "📊", "📝")),
    (r'код|code|dev|разработ|программ|hermes|сервер|server', ("💻", "⚙️", "🛠", "📝")),
    (r'деньг|финанс|бух|finance|account|1с|1c', ("💰", "📊", "📝")),
    (r'поезд|travel|тур|путешеств|авиа', ("✈️", "🌍", "📝")),
    (r'дом|home|квартир|аренд', ("🏠", "🏡", "📝")),
    (r'здоров|health|сон|sleep', ("❤️", "💊", "📝")),
)


def select_icon(title, catalog):
    """Choose only a currently advertised sticker; no arbitrary/stale IDs."""
    preferences = ("📝", "📌", "💬")
    for pattern, candidates in _ICON_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            preferences = candidates
            break
    for emoji in preferences:
        if catalog.get(emoji):
            return catalog[emoji]
    return None
