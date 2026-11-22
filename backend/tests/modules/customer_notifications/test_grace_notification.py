from typing import Union
from unittest.mock import AsyncMock, patch

import pytest
from fluent_compiler.bundle import FluentBundle

from app.core.constants import ASSETS_DIR
from app.core.enums import Locale, Role
from app.core.event_bus import EventBus
from app.modules.customer_notifications.links import customer_url
from app.modules.customer_notifications.render_args import customer_render_args
from app.modules.customer_notifications.routing import ALWAYS_BOTH
from app.modules.customer_notifications.subscribers import register_subscribers
from app.modules.subscriptions import SubscriptionGraceStartedEvent
from app.modules.users import UserDto
from tests.fakes.events import stored_event

_EVENT = SubscriptionGraceStartedEvent.__name__
_UNLIMITED = "2099-06-15T12:00:00+00:00"


def _event(grace_until: str, traffic_limit_mb: int = 0) -> SubscriptionGraceStartedEvent:
    user = UserDto(id=7, login="u", name="u", role=Role.USER, language=Locale.EN)
    return SubscriptionGraceStartedEvent(
        user=user, subscription_id=3, grace_until=grace_until, traffic_limit_mb=traffic_limit_mb
    )


async def _kiq_kwargs(event: SubscriptionGraceStartedEvent) -> dict[str, object]:
    bus = EventBus()
    register_subscribers(bus)
    kiq = AsyncMock()
    with patch("app.modules.customer_notifications.tasks.notify_customer_task.kiq", kiq):
        await bus.dispatch_stored(stored_event(event))
    return dict(kiq.await_args.kwargs)


async def test_subscriber_carries_the_date_and_traffic() -> None:
    kwargs = await _kiq_kwargs(_event("2026-12-05T00:00:00+00:00", 2048))

    assert kwargs["subscription_id"] == 3
    assert kwargs["args"] == {"grace_until": "05.12.2026", "traffic_limit_mb": 2048}


async def test_an_indefinite_grace_has_no_date() -> None:
    kwargs = await _kiq_kwargs(_event(_UNLIMITED))

    assert kwargs["args"] == {"grace_until": "none", "traffic_limit_mb": 0}


def _render(locale: Locale, args: dict[str, Union[str, int]]) -> str:
    text = (ASSETS_DIR / "translations" / locale.value / "notifications.ftl").read_text()
    bundle = FluentBundle.from_string(locale.value, text, use_isolating=False)  # type: ignore[no-untyped-call]
    body, errors = bundle.format(
        "ntf-customer-subscription-grace-started", customer_render_args(args, locale, _EVENT)
    )
    assert not errors
    return str(body)


@pytest.mark.parametrize(
    ("locale", "args", "expected"),
    [
        (
            Locale.RU,
            {"grace_until": "05.12.2026", "traffic_limit_mb": 2048},
            "Ваша подписка истекла. До 05.12.2026 действует льготный доступ: 2 ГБ. "
            "Продлите подписку, чтобы вернуть тариф.",
        ),
        (
            Locale.RU,
            {"grace_until": "05.12.2026", "traffic_limit_mb": 512},
            "Ваша подписка истекла. До 05.12.2026 действует льготный доступ: 512 МБ. "
            "Продлите подписку, чтобы вернуть тариф.",
        ),
        (
            Locale.RU,
            {"grace_until": "none", "traffic_limit_mb": 0},
            "Ваша подписка истекла. Действует льготный доступ. "
            "Продлите подписку, чтобы вернуть тариф.",
        ),
        (
            Locale.EN,
            {"grace_until": "05.12.2026", "traffic_limit_mb": 1536},
            "Your subscription has expired. Grace access is active until 05.12.2026: 1536 MB. "
            "Renew to get your plan back.",
        ),
        (
            Locale.EN,
            {"grace_until": "none", "traffic_limit_mb": 0},
            "Your subscription has expired. Grace access is active. Renew to get your plan back.",
        ),
    ],
)
def test_text(locale: Locale, args: dict[str, Union[str, int]], expected: str) -> None:
    assert _render(locale, args) == expected


def test_the_link_opens_renewal_and_both_channels_are_not_forced() -> None:
    assert customer_url(_EVENT, 3, {}) == "/?sub=3&renew=1"
    assert _EVENT not in ALWAYS_BOTH
