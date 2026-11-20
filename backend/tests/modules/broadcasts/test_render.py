from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.enums import (
    BroadcastButtonKind,
    BroadcastCategory,
    BroadcastMediaType,
    BroadcastStatus,
    Locale,
)
from app.modules.broadcasts.dto import (
    BroadcastAudienceDto,
    BroadcastButtonDto,
    BroadcastContentDto,
    BroadcastDto,
    RecipientContextDto,
    ReferralLinkBasesDto,
)
from app.modules.broadcasts.render import BroadcastRenderer, outgoing_media, recipient_values

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def _broadcast(**overrides: object) -> BroadcastDto:
    values: dict[str, object] = {
        "id": 7,
        "status": BroadcastStatus.SENDING,
        "category": BroadcastCategory.SERVICE,
        "audience": BroadcastAudienceDto(),
        "default_locale": Locale.EN,
        "contents": {
            Locale.EN: BroadcastContentDto(
                text="Hi { $name }, { $days_left } days, code { $promocode }",
                button_labels=["Site", "Get", "Menu"],
            ),
            Locale.RU: BroadcastContentDto(
                text="Привет, { $name }", button_labels=["Сайт", "Забрать", "Меню"]
            ),
        },
        "buttons": [
            BroadcastButtonDto(kind=BroadcastButtonKind.URL, url="https://x.y"),
            BroadcastButtonDto(kind=BroadcastButtonKind.PROMOCODE),
            BroadcastButtonDto(kind=BroadcastButtonKind.MENU),
        ],
        "promocode_id": 3,
    }
    values.update(overrides)
    return BroadcastDto(**values)  # type: ignore[arg-type]


def _ctx(language: Locale = Locale.EN, **overrides: object) -> RecipientContextDto:
    values: dict[str, object] = {
        "user_id": 1,
        "name": "Ann",
        "username": None,
        "language": language,
        "bonus_days": 0,
        "points": 0,
        "plan_name": "Pro",
        "expire_at": NOW + timedelta(days=3, hours=1),
        "referral_code": "abc123",
    }
    values.update(overrides)
    return RecipientContextDto(**values)  # type: ignore[arg-type]


def _texts(markup: dict) -> list[str]:
    return [row[0]["text"] for row in markup["inline_keyboard"]]


def test_renders_variables_and_buttons() -> None:
    message = BroadcastRenderer(_broadcast(), promocode_code="SALE").render(_ctx(), NOW)
    assert message.text == "Hi Ann, 3 days, code SALE"
    assert message.reply_markup is not None
    rows = message.reply_markup["inline_keyboard"]
    assert rows[0][0] == {"text": "Site", "url": "https://x.y"}
    assert rows[1][0] == {"text": "Get", "callback_data": "bc:promo:7"}
    assert rows[2][0] == {"text": "Menu", "callback_data": "bc:menu"}


def test_close_button_deletes_in_bot_and_is_dropped_in_channel() -> None:
    broadcast = _broadcast(
        contents={Locale.EN: BroadcastContentDto(text="Hi", button_labels=["Close"])},
        buttons=[BroadcastButtonDto(kind=BroadcastButtonKind.CLOSE)],
    )
    renderer = BroadcastRenderer(broadcast, promocode_code=None)
    assert (renderer.render(_ctx(), NOW).reply_markup or {})["inline_keyboard"] == [
        [{"text": "Close", "callback_data": "cn:close"}]
    ]
    assert renderer.render_channel("shop_bot").reply_markup is None


def test_button_icon_goes_to_icon_custom_emoji_id() -> None:
    broadcast = _broadcast(
        buttons=[
            BroadcastButtonDto(
                kind=BroadcastButtonKind.URL,
                url="https://x.y",
                icon_custom_emoji_id="5368324170671202286",
            ),
            BroadcastButtonDto(kind=BroadcastButtonKind.PROMOCODE),
            BroadcastButtonDto(kind=BroadcastButtonKind.MENU),
        ]
    )
    rows = (
        BroadcastRenderer(broadcast, promocode_code="SALE").render(_ctx(), NOW).reply_markup or {}
    )["inline_keyboard"]
    assert rows[0][0] == {
        "text": "Site",
        "url": "https://x.y",
        "icon_custom_emoji_id": "5368324170671202286",
    }
    assert "icon_custom_emoji_id" not in rows[1][0]


def test_locale_variant_and_fallback() -> None:
    renderer = BroadcastRenderer(_broadcast(), promocode_code="SALE")
    assert renderer.render(_ctx(Locale.RU), NOW).text == "Привет, Ann"
    fallback = renderer.render(_ctx(Locale.DE), NOW)
    assert fallback.text.startswith("Hi Ann") and _texts(fallback.reply_markup or {})[0] == "Site"


def test_dropped_promocode_removes_button_and_value() -> None:
    message = BroadcastRenderer(_broadcast(), promocode_code=None).render(_ctx(), NOW)
    assert message.text.endswith("code —")
    assert "Get" not in _texts(message.reply_markup or {})


def test_marketing_has_no_unsubscribe_button_in_telegram() -> None:
    # the unsubscribe link lives in marketing emails; Telegram gets none for now
    renderer = BroadcastRenderer(
        _broadcast(category=BroadcastCategory.MARKETING), promocode_code="SALE"
    )
    rows = (renderer.render(_ctx(Locale.RU), NOW).reply_markup or {})["inline_keyboard"]
    assert all(row[0].get("callback_data") != "bc:unsub" for row in rows)


def test_no_buttons_means_no_markup() -> None:
    broadcast = _broadcast(
        buttons=[], contents={Locale.EN: BroadcastContentDto(text="x")}, promocode_id=None
    )
    assert (
        BroadcastRenderer(broadcast, promocode_code=None).render(_ctx(), NOW).reply_markup is None
    )


def test_channel_post_uses_deep_links() -> None:
    renderer = BroadcastRenderer(_broadcast(), promocode_code="SALE")
    rows = (renderer.render_channel("shop_bot").reply_markup or {})["inline_keyboard"]
    assert rows[0][0] == {"text": "Site", "url": "https://x.y"}
    assert rows[1][0] == {"text": "Get", "url": "https://t.me/shop_bot?start=bc_7"}
    assert rows[2][0] == {"text": "Menu", "url": "https://t.me/shop_bot?start=menu"}


def test_channel_post_without_username_omits_bot_buttons() -> None:
    renderer = BroadcastRenderer(_broadcast(), promocode_code="SALE")
    assert _texts(renderer.render_channel(None).reply_markup or {}) == ["Site"]


def test_outgoing_media_path_includes_the_version() -> None:
    broadcast = _broadcast(
        media_type=BroadcastMediaType.PHOTO, media_extension="jpg", media_version=3
    )
    media = outgoing_media(Path("/media"), broadcast)
    assert media is not None
    assert media.path == Path("/media/broadcasts/7_3.jpg")


def test_an_unlimited_subscription_has_no_expiry_variables() -> None:
    values = recipient_values(_ctx(expire_at=NOW.replace(year=2099)), NOW, None)

    assert values["expire_date"] == ""
    assert values["days_left"] == ""


_BASES = ReferralLinkBasesDto(
    bot="https://t.me/shop_bot?start=ref_", cabinet="https://c.example/?ref="
)


def _referral_broadcast() -> BroadcastDto:
    return _broadcast(
        contents={
            Locale.EN: BroadcastContentDto(
                text="Invite: { $referral_link }",
                button_labels=["Share"],
            )
        },
        buttons=[BroadcastButtonDto(kind=BroadcastButtonKind.SHARE_REFERRAL)],
        promocode_id=None,
    )


def test_referral_link_and_share_button() -> None:
    renderer = BroadcastRenderer(_referral_broadcast(), promocode_code=None, referral_bases=_BASES)
    message = renderer.render(_ctx(), NOW)

    assert message.text == "Invite: https://t.me/shop_bot?start=ref_abc123"
    assert (message.reply_markup or {})["inline_keyboard"] == [
        [
            {
                "text": "Share",
                "url": "https://t.me/share/url?url=https%3A%2F%2Ft.me%2Fshop_bot%3Fstart%3Dref_abc123",
            }
        ]
    ]


def test_without_referral_link_the_share_button_is_dropped() -> None:
    # referrals off or the bot username unknown
    message = BroadcastRenderer(_referral_broadcast(), promocode_code=None).render(_ctx(), NOW)
    assert message.text == "Invite: —"
    assert message.reply_markup is None
    # a customer with no code
    message = BroadcastRenderer(
        _referral_broadcast(), promocode_code=None, referral_bases=_BASES
    ).render(_ctx(referral_code=None), NOW)
    assert message.text == "Invite: —"
    assert message.reply_markup is None


def test_channel_post_has_no_share_button() -> None:
    renderer = BroadcastRenderer(_referral_broadcast(), promocode_code=None, referral_bases=_BASES)
    assert renderer.render_channel("shop_bot").reply_markup is None
