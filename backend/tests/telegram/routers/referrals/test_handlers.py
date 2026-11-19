from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import CallbackQuery, Message

from app.core.constants import USER_KEY
from app.core.enums import Locale
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.dialogs import catalog
from app.modules.referrals import MAX_BONUS_DAYS, RedeemBonusDaysDto
from app.telegram.dialogs import state_pool
from app.telegram.routers.menu.catalog import register_menu_catalog
from app.telegram.routers.purchase.catalog import register_purchase_catalog
from app.telegram.routers.referrals.catalog import register_referrals_catalog
from app.telegram.routers.referrals.getters import REDEEM_DAYS_KEY
from app.telegram.routers.referrals.handlers import (
    on_redeem_all_days,
    on_redeem_days,
    on_redeem_days_input,
    on_redeem_target_selected,
    on_referral_qr,
    on_referrals_start,
)
from app.telegram.routers.subscriptions.catalog import register_subscriptions_catalog
from app.telegram.states import MainMenu, Referrals

_LOCALES_DIR = Path(__file__).parents[4] / "assets" / "translations"


def _manager(user_bonus_days: int = 0, item_id: str = "") -> AsyncMock:
    manager = AsyncMock()
    manager.middleware_data = {
        USER_KEY: MagicMock(id=1, language=Locale.EN, bonus_days=user_bonus_days)
    }
    manager.dialog_data = {}
    manager.item_id = item_id
    return manager


def _i18n() -> MagicMock:
    i18n = MagicMock()
    i18n.get_translator_by_locale.return_value = MagicMock(
        get=lambda key, **kwargs: f"{key}{kwargs}" if kwargs else key
    )
    return i18n


def _message(text: str) -> MagicMock:
    message = MagicMock(spec=Message)
    message.text = text
    message.answer = AsyncMock()
    return message


async def test_redeem_opens_the_amount_window() -> None:
    manager = _manager(user_bonus_days=12)

    await on_redeem_days.__dishka_orig_func__(
        AsyncMock(), MagicMock(), manager, AsyncMock(return_value=[MagicMock(id=4)]), _i18n()
    )

    manager.switch_to.assert_awaited_once_with(Referrals.REDEEM_AMOUNT)


async def test_redeem_without_a_subscription_alerts() -> None:
    callback = AsyncMock()
    manager = _manager(user_bonus_days=3)

    await on_redeem_days.__dishka_orig_func__(
        callback, MagicMock(), manager, AsyncMock(return_value=[]), _i18n()
    )

    manager.switch_to.assert_not_awaited()
    callback.answer.assert_awaited_once_with("ntf-referrals.no-subscription")


async def test_typed_amount_is_redeemed_into_the_only_subscription() -> None:
    manager = _manager(user_bonus_days=12)
    redeem = AsyncMock()
    message = _message(" 5 ")

    await on_redeem_days_input.__dishka_orig_func__(
        message, MagicMock(), manager, redeem, AsyncMock(return_value=[MagicMock(id=4)]), _i18n()
    )

    assert redeem.await_args.args[1] == RedeemBonusDaysDto(subscription_id=4, days=5)
    manager.switch_to.assert_awaited_once_with(Referrals.MAIN)


async def test_invalid_amount_is_refused() -> None:
    for text in ("0", "-1", "abc", "13", "2.5", ""):
        manager = _manager(user_bonus_days=12)
        redeem = AsyncMock()

        await on_redeem_days_input.__dishka_orig_func__(
            _message(text), MagicMock(), manager, redeem, AsyncMock(), _i18n()
        )

        redeem.assert_not_awaited()
        manager.switch_to.assert_not_awaited()


async def test_redeem_all_takes_at_most_one_renewals_worth_of_days() -> None:
    manager = _manager(user_bonus_days=MAX_BONUS_DAYS + 5)
    redeem = AsyncMock()

    await on_redeem_all_days.__dishka_orig_func__(
        AsyncMock(),
        MagicMock(),
        manager,
        redeem,
        AsyncMock(return_value=[MagicMock(id=4)]),
        _i18n(),
    )

    assert redeem.await_args.args[1] == RedeemBonusDaysDto(subscription_id=4, days=MAX_BONUS_DAYS)


async def test_amount_with_several_subscriptions_asks_for_the_target() -> None:
    manager = _manager(user_bonus_days=12)
    redeem = AsyncMock()
    targets = AsyncMock(return_value=[MagicMock(id=4), MagicMock(id=5)])

    await on_redeem_days_input.__dishka_orig_func__(
        _message("5"), MagicMock(), manager, redeem, targets, _i18n()
    )

    redeem.assert_not_awaited()
    manager.switch_to.assert_awaited_once_with(Referrals.REDEEM_TARGET)
    assert manager.dialog_data[REDEEM_DAYS_KEY] == 5


async def test_redeem_the_chosen_amount_into_the_chosen_subscription() -> None:
    manager = _manager(user_bonus_days=7, item_id="5")
    manager.dialog_data[REDEEM_DAYS_KEY] = 3
    redeem = AsyncMock()

    await on_redeem_target_selected.__dishka_orig_func__(
        AsyncMock(), MagicMock(), manager, redeem, _i18n()
    )

    assert redeem.await_args.args[1] == RedeemBonusDaysDto(subscription_id=5, days=3)
    manager.switch_to.assert_awaited_once_with(Referrals.MAIN)


async def test_balance_spent_since_the_amount_was_chosen_caps_the_redemption() -> None:
    manager = _manager(user_bonus_days=2, item_id="5")
    manager.dialog_data[REDEEM_DAYS_KEY] = 6
    redeem = AsyncMock()

    await on_redeem_target_selected.__dishka_orig_func__(
        AsyncMock(), MagicMock(), manager, redeem, _i18n()
    )

    assert redeem.await_args.args[1] == RedeemBonusDaysDto(subscription_id=5, days=2)


async def test_redeem_refusal_is_reported() -> None:
    callback = AsyncMock()
    redeem = AsyncMock(
        side_effect=ValidationError("trial", code=ErrorCode.BONUS_DAYS_TRIAL_SUBSCRIPTION)
    )

    await on_redeem_all_days.__dishka_orig_func__(
        callback,
        MagicMock(),
        _manager(user_bonus_days=3),
        redeem,
        AsyncMock(return_value=[MagicMock(id=4)]),
        _i18n(),
    )

    callback.answer.assert_awaited_once_with("ntf-referrals.redeem-failed")


def test_every_referral_notification_exists_in_each_locale() -> None:
    attributes = (
        "no-subscription",
        "days-redeemed",
        "redeem-failed",
        "invalid-days",
    )
    for menu in _LOCALES_DIR.glob("*/menu.ftl"):
        text = menu.read_text()
        section = text.split("ntf-referrals =", 1)[1].split("\n\n", 1)[0]
        assert all(f"    .{attribute} =" in section for attribute in attributes), menu


def test_every_catalog_action_has_a_bound_handler() -> None:
    catalog._reset_for_tests()
    state_pool._reset_for_tests()
    try:
        register_menu_catalog()
        register_subscriptions_catalog()
        register_purchase_catalog()
        register_referrals_catalog()
        assert {
            key
            for key in catalog.action_keys()
            # an inline action opens the chat picker, nothing to call back
            if not catalog.has_action_handler(key) and catalog.inline_query_for(key) is None
        } == set()
    finally:
        catalog._reset_for_tests()
        state_pool._reset_for_tests()


async def test_qr_sends_the_referral_link_as_a_png() -> None:
    callback = MagicMock(spec=CallbackQuery)
    callback.message = MagicMock(spec=Message)
    callback.message.answer_photo = AsyncMock()
    callback.answer = AsyncMock()
    bot = MagicMock(me=AsyncMock(return_value=MagicMock(username="shop_bot")))

    await on_referral_qr.__dishka_orig_func__(
        callback,
        MagicMock(),
        _manager(),
        AsyncMock(return_value=MagicMock(referral_code="abc")),
        bot,
    )

    photo = callback.message.answer_photo.await_args.args[0]
    assert photo.data.startswith(b"\x89PNG")
    callback.answer.assert_awaited_once()


async def test_referrals_window_sends_back_to_the_menu_with_the_program_off(
    monkeypatch,
) -> None:
    notices: list[str] = []

    async def _notice(event: object, text: str) -> None:
        notices.append(text)

    monkeypatch.setattr("app.telegram.routers.referrals.handlers.answer_notice", _notice)
    i18n = MagicMock()
    i18n.get_translator_by_locale.return_value = MagicMock(get=lambda key, **kwargs: key)
    for enabled in (True, False):
        manager = AsyncMock()
        manager.middleware_data = {USER_KEY: MagicMock(language=Locale.EN)}
        settings = MagicMock(system=AsyncMock(return_value=MagicMock(enabled=enabled)))
        await on_referrals_start.__dishka_orig_func__(None, manager, settings, i18n)
        assert manager.start.await_count == (0 if enabled else 1)
    assert manager.start.await_args.args == (MainMenu.MAIN,)
    assert notices == ["ntf-referrals.disabled"]
