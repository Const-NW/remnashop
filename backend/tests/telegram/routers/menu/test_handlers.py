from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import ReplyKeyboardRemove
from aiogram_dialog import StartMode
from remnapy.enums import TrafficLimitStrategy

from app.core.constants import USER_KEY
from app.core.enums import Currency, PurchaseType, SubscriptionStatus
from app.modules.subscriptions.dto import GetAvailableTrialDto
from app.telegram.dialogs.context import SELECTED_SUBSCRIPTION_KEY
from app.telegram.routers.menu import handlers
from app.telegram.routers.menu.handlers import (
    on_get_trial,
    on_show_connect_problem,
    on_start_command,
)
from app.telegram.routers.purchase.handlers import PLAN_CODE_KEY
from app.telegram.states import Purchase, Subscriptions


def _manager() -> MagicMock:
    manager = MagicMock()
    manager.middleware_data = {USER_KEY: MagicMock(id=1)}
    manager.start = AsyncMock()
    manager.switch_to = AsyncMock()
    return manager


def _trial(price: Decimal) -> GetAvailableTrialDto:
    return GetAvailableTrialDto(
        plan=MagicMock(public_code="trialCode"),
        is_trial_available=True,
        price=price,
        currency=Currency.USD,
    )


async def test_a_free_trial_is_activated_on_the_spot() -> None:
    manager = _manager()
    activate = AsyncMock(return_value=MagicMock(id=7))

    await on_get_trial.__dishka_orig_func__(
        MagicMock(answer=AsyncMock()),
        MagicMock(),
        manager,
        get_available_trial=AsyncMock(return_value=_trial(Decimal(0))),
        activate_trial_subscription=activate,
        i18n=MagicMock(),
    )

    activate.assert_awaited_once()
    # the card holds "Connect", which the menu lacks when several subscriptions are allowed
    manager.start.assert_awaited_once_with(
        Subscriptions.CARD, data={SELECTED_SUBSCRIPTION_KEY: 7}, mode=StartMode.RESET_STACK
    )


async def test_a_paid_trial_opens_its_purchase() -> None:
    manager = _manager()
    activate = AsyncMock()

    await on_get_trial.__dishka_orig_func__(
        MagicMock(answer=AsyncMock()),
        MagicMock(),
        manager,
        get_available_trial=AsyncMock(return_value=_trial(Decimal("1.5"))),
        activate_trial_subscription=activate,
        i18n=MagicMock(),
    )

    activate.assert_not_awaited()
    manager.start.assert_awaited_once_with(
        Purchase.PLAN,
        data={"purchase_type": PurchaseType.NEW.value, PLAN_CODE_KEY: "trialCode"},
    )


_NOW = datetime(2026, 11, 12, 15, 30, tzinfo=timezone.utc)  # a Thursday


def _subscription(
    status: SubscriptionStatus,
    *,
    is_trial: bool = False,
    strategy: TrafficLimitStrategy = TrafficLimitStrategy.NO_RESET,
) -> MagicMock:
    return MagicMock(
        id=7,
        status=status,
        is_trial=is_trial,
        traffic_limit_strategy=strategy,
        created_at=_NOW,
    )


async def _show(
    subscription: MagicMock, manager: MagicMock, anchor: Optional[AsyncMock] = None
) -> MagicMock:
    callback = MagicMock(answer=AsyncMock())
    runner = MagicMock(get=lambda key, **kwargs: f"{key}{sorted(kwargs.items())}")
    await on_show_connect_problem.__dishka_orig_func__(
        callback,
        MagicMock(),
        manager,
        list_own_active_subscriptions=AsyncMock(return_value=[subscription]),
        list_own_subscriptions=AsyncMock(return_value=[subscription]),
        get_traffic_reset_anchor=anchor or AsyncMock(return_value=None),
        i18n=MagicMock(get_translator_by_locale=MagicMock(return_value=runner)),
    )
    return callback


async def test_the_warning_explains_itself_in_an_alert() -> None:
    callback = await _show(_subscription(SubscriptionStatus.EXPIRED, is_trial=True), _manager())

    callback.answer.assert_awaited_once_with(
        "ntf-common.connect-not-available"
        "[('is_trial', 1), ('reset_in', 'none'), ('status', 'EXPIRED')]",
        show_alert=True,
    )


async def test_a_warning_left_after_the_fix_just_redraws_the_menu() -> None:
    manager = _manager()
    manager.show = AsyncMock()

    callback = await _show(_subscription(SubscriptionStatus.ACTIVE), manager)

    callback.answer.assert_not_awaited()
    manager.show.assert_awaited_once()


async def test_a_rolling_reset_counts_from_the_panel_user(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def _reset(strategy: object, created_at: object, now: object) -> None:
        seen.append(created_at)

    monkeypatch.setattr("app.telegram.routers.menu.handlers.next_traffic_reset", _reset)
    panel_created = datetime(2025, 3, 31, tzinfo=timezone.utc)
    limited = SubscriptionStatus.LIMITED
    anchor = AsyncMock(return_value=panel_created)

    await _show(
        _subscription(limited, strategy=TrafficLimitStrategy.MONTH_ROLLING), _manager(), anchor
    )
    # no anchor anywhere: no date rather than our own row's
    await _show(_subscription(limited, strategy=TrafficLimitStrategy.MONTH_ROLLING), _manager())
    # other strategies don't need one
    await _show(_subscription(limited, strategy=TrafficLimitStrategy.MONTH), _manager(), anchor)

    assert seen == [panel_created, None, None]
    assert anchor.await_count == 1


@pytest.mark.parametrize("enabled", [True, False])
async def test_start_takes_away_an_old_bot_s_keyboard_only_when_enabled(
    enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "get_translator", MagicMock())
    monkeypatch.setattr(handlers, "render_window_text", lambda runner, key: key)
    message = MagicMock(answer=AsyncMock())
    manager = _manager()

    await on_start_command(
        message, MagicMock(), manager, remove_reply_keyboard=enabled, i18n=MagicMock()
    )

    if enabled:
        message.answer.assert_awaited_once()
        args, kwargs = message.answer.await_args
        assert args == ("msg-start-welcome",)
        assert isinstance(kwargs["reply_markup"], ReplyKeyboardRemove)
    else:
        message.answer.assert_not_awaited()
    manager.start.assert_awaited_once()
