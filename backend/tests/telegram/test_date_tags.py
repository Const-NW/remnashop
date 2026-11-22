from datetime import datetime, timezone

from fluentogram import TranslatorRunner

from app.core.constants import ASSETS_DIR
from app.core.enums import Locale
from app.core.error_codes import ErrorCode
from app.core.utils.telegram_text import validate_telegram_html
from app.telegram.dialogs.rendering import render_window_text
from app.telegram.i18n import (
    build_translator_hub,
    expiry_unix_time,
    format_date_tag,
    format_expiry_tag,
)

_DATE = datetime(2026, 12, 22, 16, 3, tzinfo=timezone.utc)
_UNLIMITED = datetime(2999, 1, 1, tzinfo=timezone.utc)


def _runner() -> TranslatorRunner:
    text = (ASSETS_DIR / "translations" / "ru" / "menu.ftl").read_text()
    hub = build_translator_hub([Locale.RU], {Locale.RU: text}, Locale.RU)
    return hub.get_translator_by_locale(Locale.RU.value)


def test_date_tag_carries_the_timestamp_and_utc_fallback() -> None:
    assert format_date_tag(_DATE) == '<tg-time unix="1797955380" format="d">22.12.2026</tg-time>'
    assert format_date_tag(_DATE, with_time=True) == (
        '<tg-time unix="1797955380" format="dt">22.12.2026 16:03</tg-time>'
    )


def test_expiry_tag_is_a_word_for_an_unlimited_subscription() -> None:
    runner = _runner()

    assert format_expiry_tag(runner, _UNLIMITED) == "бессрочно"
    assert format_expiry_tag(runner, _DATE, with_time=True) == format_date_tag(
        _DATE, with_time=True
    )


def test_tags_pass_the_window_html_check() -> None:
    runner = _runner()
    for text in (format_date_tag(_DATE), format_expiry_tag(runner, _DATE, with_time=True)):
        validate_telegram_html(text, code=ErrorCode.TRANSLATION_HTML_INVALID)


_OWNER_TEMPLATE = """
msg-test = Действует до: { $is_unlimited ->
    [1] бессрочно
   *[0] <tg-time unix="{ $expire_at_unix }" format="wDT">{ $expire_at }</tg-time>
}
"""


def _render(**kwargs: object) -> str:
    validate_telegram_html(_OWNER_TEMPLATE, code=ErrorCode.TRANSLATION_HTML_INVALID)
    hub = build_translator_hub([Locale.RU], {Locale.RU: _OWNER_TEMPLATE}, Locale.RU)
    return render_window_text(hub.get_translator_by_locale(Locale.RU.value), "msg-test", **kwargs)


def test_owner_template_formats_the_raw_timestamp_itself() -> None:
    text = _render(is_unlimited=0, expire_at_unix=expiry_unix_time(_DATE), expire_at="22.12.2026")

    assert text == 'Действует до: <tg-time unix="1797955380" format="wDT">22.12.2026</tg-time>'
    validate_telegram_html(text, code=ErrorCode.TRANSLATION_HTML_INVALID)


def test_owner_template_shows_a_word_for_an_unlimited_subscription() -> None:
    text = _render(is_unlimited=1, expire_at_unix=expiry_unix_time(_UNLIMITED), expire_at="x")

    assert text == "Действует до: бессрочно"
