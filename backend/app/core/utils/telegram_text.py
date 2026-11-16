import html
import re
from html.parser import HTMLParser
from typing import Final, Optional

from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError

PREMIUM_EMOJI_RE: Final[re.Pattern[str]] = re.compile(r"\[([^\[\]]{1,16})\]\(emoji:(\d{1,24})\)")
TELEGRAM_TEXT_LIMIT: Final[int] = 4096
TELEGRAM_CAPTION_LIMIT: Final[int] = 1024
_ALLOWED_TAGS: Final[frozenset[str]] = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "a",
        "code",
        "pre",
        "blockquote",
        "tg-spoiler",
    }
)
_URL_SCHEMES: Final[tuple[str, ...]] = ("https://", "http://", "tg://")
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^<>]*>")


def premium_emoji_html(text: str) -> str:
    return PREMIUM_EMOJI_RE.sub(r'<tg-emoji emoji-id="\2">\1</tg-emoji>', text)


def plain_to_html(text: str) -> str:
    """Plain translation text as Telegram HTML: escaped, premium emoji kept."""
    return premium_emoji_html(html.escape(text))


def premium_emoji_glyphs(text: str) -> str:
    # alerts, command descriptions and inline titles can't show a premium emoji
    return PREMIUM_EMOJI_RE.sub(r"\1", text)


def html_to_plain(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text))


def telegram_length(text: str) -> int:
    # Telegram counts UTF-16 code units; premium emoji markup is sent as its glyph
    sent = PREMIUM_EMOJI_RE.sub(r"\1", text)
    return len(sent.encode("utf-16-le")) // 2


def split_button_icon(label: str) -> tuple[str, Optional[str]]:
    """A button label as Telegram takes it, and the premium emoji id it opens with.
    A custom emoji shows on a button only as its icon, before the text, so one
    anywhere else goes out as its glyph."""
    label = label.strip()
    icon_id = None
    if (match := PREMIUM_EMOJI_RE.match(label)) and label[match.end() :].strip():
        icon_id = match.group(2)
        label = label[match.end() :].lstrip()
    return PREMIUM_EMOJI_RE.sub(r"\1", label), icon_id


def compose_button_text(
    label: str, icon: Optional[str], icon_id: Optional[str]
) -> tuple[str, Optional[str]]:
    """A button's text and premium icon id. A premium emoji opening the label is
    the icon, ahead of the picked one. Otherwise the picked premium id wins over
    the picked glyph, which is kept for the dashboard: adding it to the text
    would show the emoji twice."""
    text, leading_id = split_button_icon(label)
    if leading_id:
        return text, leading_id
    return (f"{icon} {text}" if icon and not icon_id else text), icon_id


def html_text_length(text: str) -> int:
    return telegram_length(html_to_plain(text))


class _TagChecker(HTMLParser):
    def __init__(self, code: ErrorCode) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []
        self._code = code

    def invalid(self, reason: str) -> ValidationError:
        return ValidationError(f"Invalid HTML: {reason}", code=self._code)

    def handle_data(self, data: str) -> None:
        if "<" in data:
            raise self.invalid("bare < in text data must be escaped as &lt;")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag not in _ALLOWED_TAGS:
            raise self.invalid(f"<{tag}> is not supported")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if not href.startswith(_URL_SCHEMES):
                raise self.invalid("link must start with https://, http:// or tg://")
        self.stack.append(tag)

    def handle_comment(self, data: str) -> None:
        raise self.invalid("comments are not supported")

    def handle_decl(self, decl: str) -> None:
        raise self.invalid("declarations are not supported")

    def handle_pi(self, data: str) -> None:
        raise self.invalid("processing instructions are not supported")

    def unknown_decl(self, data: str) -> None:
        raise self.invalid("declarations are not supported")

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            raise self.invalid(f"unexpected </{tag}>")
        self.stack.pop()


def validate_telegram_html(text: str, *, code: ErrorCode) -> None:
    checker = _TagChecker(code)
    checker.feed(text)
    checker.close()
    if checker.stack:
        raise checker.invalid(f"<{checker.stack[-1]}> is not closed")
