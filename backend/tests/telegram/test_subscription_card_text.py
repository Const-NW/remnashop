from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from app.core.constants import ASSETS_DIR
from app.core.enums import Locale, SubscriptionStatus
from app.telegram.dialogs.rendering import TrustedHtml, render_window_text
from app.telegram.i18n import build_translator_hub
from app.telegram.routers.subscriptions.getters import card_grace, grace_line


def _card(**kwargs: object) -> str:
    text = (ASSETS_DIR / "translations" / "en" / "menu.ftl").read_text()
    hub = build_translator_hub([Locale.EN], {Locale.EN: text}, Locale.EN)
    runner = hub.get_translator_by_locale(Locale.EN.value)
    args: dict[str, object] = {
        "plan_name": "Pro",
        "status": SubscriptionStatus.ACTIVE.value,
        "is_trial": 0,
        "expire_at": "01.01.2027",
        "expire_at_local": "01.01.2027",
        "traffic_limit": 0,
        "device_limit": 0,
        "grace": "",
    }
    text = runner.get("msg-subscription-card", **{**args, **kwargs})
    return text.replace("\u2068", "").replace("\u2069", "")


def test_card_shows_localized_status_and_unlimited_limits() -> None:
    text = _card()

    assert "<b>Status</b>: 🟢 Active" in text
    assert "<b>Traffic limit</b>: Unlimited" in text
    assert "<b>Device limit</b>: Unlimited" in text


def test_card_shows_limits_with_units() -> None:
    text = _card(traffic_limit=50, device_limit=3)

    assert "<b>Traffic limit</b>: 50 GB" in text
    assert "<b>Device limit</b>: 3" in text


def _rendered(**kwargs: object) -> str:
    text = (ASSETS_DIR / "translations" / "en" / "menu.ftl").read_text()
    hub = build_translator_hub([Locale.EN], {Locale.EN: text}, Locale.EN)
    args: dict[str, object] = {
        "plan_name": "Pro",
        "status": SubscriptionStatus.ACTIVE.value,
        "is_trial": 0,
        "expire_at": "01.01.2027",
        "expire_at_local": "01.01.2027",
        "traffic_limit": 50,
        "device_limit": 3,
        "grace": "",
    }
    runner = hub.get_translator_by_locale(Locale.EN.value)
    return render_window_text(runner, "msg-subscription-card", **{**args, **kwargs})


def test_card_names_a_trial() -> None:
    assert _rendered(is_trial=1).startswith("<b>🎁 Trial subscription · Pro</b>")
    assert _rendered().startswith("<b>💳 Subscription · Pro</b>")


def test_card_shows_a_grace_line_only_in_grace() -> None:
    assert "Grace period until 05.12.2026" in _rendered(grace="Grace period until 05.12.2026")
    assert "Grace" not in _rendered(grace="")


def _grace_line(grace_until: Optional[datetime]) -> str:
    text = (ASSETS_DIR / "translations" / "en" / "menu.ftl").read_text()
    hub = build_translator_hub([Locale.EN], {Locale.EN: text}, Locale.EN)
    runner = hub.get_translator_by_locale(Locale.EN.value)
    subscription = SimpleNamespace(grace_until=grace_until)
    return grace_line(runner, subscription).replace("\u2068", "").replace("\u2069", "")  # type: ignore[arg-type]


def test_grace_line_is_trusted_html() -> None:
    text = (ASSETS_DIR / "translations" / "en" / "menu.ftl").read_text()
    hub = build_translator_hub([Locale.EN], {Locale.EN: text}, Locale.EN)
    runner = hub.get_translator_by_locale(Locale.EN.value)
    subscription = SimpleNamespace(grace_until=datetime(2026, 12, 5, tzinfo=timezone.utc))

    assert isinstance(grace_line(runner, subscription), TrustedHtml)  # type: ignore[arg-type]


def test_grace_line_names_the_date_or_nothing() -> None:
    dated = _grace_line(datetime(2026, 12, 5, tzinfo=timezone.utc))
    assert dated == "• Grace period until 05.12.2026"
    assert _grace_line(datetime(2099, 12, 5, tzinfo=timezone.utc)) == "• Grace period"
    assert _grace_line(None) == ""


def test_card_sets_the_grace_line_off_with_an_empty_line() -> None:
    text = (ASSETS_DIR / "translations" / "en" / "menu.ftl").read_text()
    hub = build_translator_hub([Locale.EN], {Locale.EN: text}, Locale.EN)
    runner = hub.get_translator_by_locale(Locale.EN.value)
    grace = SimpleNamespace(grace_until=datetime(2026, 12, 5, tzinfo=timezone.utc))

    card = _rendered(grace=card_grace(runner, grace))  # type: ignore[arg-type]
    plain = _rendered(grace=card_grace(runner, SimpleNamespace(grace_until=None)))  # type: ignore[arg-type]

    card = card.replace("\u2068", "").replace("\u2069", "")
    assert "01.01.2027\n\n• <b>Grace period</b> until 05.12.2026</blockquote>" in card
    assert "01.01.2027</blockquote>" in plain
