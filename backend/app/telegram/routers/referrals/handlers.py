import io
from typing import Union

import segno
from aiogram import Bot
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram_dialog import Data, DialogManager, ShowMode, StartMode
from aiogram_dialog.widgets.input import MessageInput
from aiogram_dialog.widgets.kbd import Button, Keyboard
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from fluentogram import TranslatorHub, TranslatorRunner

from app.core.constants import USER_KEY
from app.core.exceptions import CooldownError, NotFoundError, ValidationError
from app.modules.referrals import (
    MAX_BONUS_DAYS,
    GetOwnReferralStatsUseCase,
    GetReferralSettingsUseCase,
    ListBonusDaysTargetsUseCase,
    RedeemBonusDaysDto,
    RedeemBonusDaysUseCase,
    ResetOwnReferralCodeUseCase,
)
from app.modules.users import UserDto
from app.telegram.i18n import format_wait, get_translator
from app.telegram.notice import answer_notice
from app.telegram.routers.referrals.getters import REDEEM_DAYS_KEY, referral_url
from app.telegram.states import MainMenu, Referrals


@inject
async def on_referrals_start(
    start_data: Data,
    manager: DialogManager,
    get_referral_settings: FromDishka[GetReferralSettingsUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    # a button can lead here with the program off: an old menu message, or one an admin placed
    if (await get_referral_settings.system()).enabled:
        return
    user: UserDto = manager.middleware_data[USER_KEY]
    await answer_notice(manager.event, get_translator(i18n, user).get("ntf-referrals.disabled"))
    await manager.start(MainMenu.MAIN, mode=StartMode.RESET_STACK)


@inject
async def on_referral_qr(
    callback: CallbackQuery,
    widget: Keyboard,
    dialog_manager: DialogManager,
    get_own_referral_stats: FromDishka[GetOwnReferralStatsUseCase],
    bot: FromDishka[Bot],
) -> None:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    stats = await get_own_referral_stats(user)
    me = await bot.me()
    if stats.referral_code and me.username and isinstance(callback.message, Message):
        image = io.BytesIO()
        segno.make(referral_url(me.username, stats.referral_code), error="m").save(
            image, kind="png", scale=10, border=2
        )
        await callback.message.answer_photo(
            BufferedInputFile(image.getvalue(), filename="referral-qr.png")
        )
    await callback.answer()


@inject
async def on_reset_referral_code(
    callback: CallbackQuery,
    widget: Keyboard,
    dialog_manager: DialogManager,
    reset_own_referral_code: FromDishka[ResetOwnReferralCodeUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    runner = get_translator(i18n, user)
    try:
        await reset_own_referral_code(user)
    except CooldownError as error:
        await answer_notice(
            callback,
            runner.get(
                "ntf-referrals.reset-cooldown", wait=format_wait(runner, error.available_at)
            ),
        )
    except ValidationError:
        # switched off while the window was open: the main window comes back without the button
        pass
    else:
        await answer_notice(callback, runner.get("ntf-referrals.code-reset"))
    await dialog_manager.switch_to(Referrals.MAIN)


def _redeemable_days(user: UserDto) -> int:
    # one renewal takes at most MAX_BONUS_DAYS, the rest stays on the balance
    return min(user.bonus_days, MAX_BONUS_DAYS)


async def _redeem(
    event: Union[CallbackQuery, Message],
    dialog_manager: DialogManager,
    redeem_bonus_days: RedeemBonusDaysUseCase,
    runner: TranslatorRunner,
    subscription_id: int,
) -> None:
    # the middleware loads the user on every update, so this balance is current
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    days = min(dialog_manager.dialog_data.get(REDEEM_DAYS_KEY, 0), _redeemable_days(user))
    # a button left on an old screen; the usecase would renew by zero days
    if days <= 0:
        await answer_notice(event, runner.get("ntf-referrals.redeem-failed"))
        return
    try:
        await redeem_bonus_days(
            user, RedeemBonusDaysDto(subscription_id=subscription_id, days=days)
        )
    except (NotFoundError, ValidationError):
        await answer_notice(event, runner.get("ntf-referrals.redeem-failed"))
        return
    await answer_notice(event, runner.get("ntf-referrals.days-redeemed"))
    await dialog_manager.switch_to(Referrals.MAIN)


async def _redeem_days(
    event: Union[CallbackQuery, Message],
    dialog_manager: DialogManager,
    redeem_bonus_days: RedeemBonusDaysUseCase,
    list_bonus_days_targets: ListBonusDaysTargetsUseCase,
    runner: TranslatorRunner,
    days: int,
) -> None:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    dialog_manager.dialog_data[REDEEM_DAYS_KEY] = days
    # what the days can extend: no trial, no disabled one, none whose traffic never comes back
    targets = await list_bonus_days_targets(user)
    if not targets:
        await answer_notice(event, runner.get("ntf-referrals.no-subscription"))
    elif len(targets) == 1:
        await _redeem(event, dialog_manager, redeem_bonus_days, runner, targets[0].id)
    else:
        await dialog_manager.switch_to(Referrals.REDEEM_TARGET)


@inject
async def on_redeem_days(
    callback: CallbackQuery,
    widget: Keyboard,
    dialog_manager: DialogManager,
    list_bonus_days_targets: FromDishka[ListBonusDaysTargetsUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    if not await list_bonus_days_targets(user):
        runner = get_translator(i18n, user)
        await answer_notice(callback, runner.get("ntf-referrals.no-subscription"))
        return
    await dialog_manager.switch_to(Referrals.REDEEM_AMOUNT)


@inject
async def on_redeem_all_days(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    redeem_bonus_days: FromDishka[RedeemBonusDaysUseCase],
    list_bonus_days_targets: FromDishka[ListBonusDaysTargetsUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    await _redeem_days(
        callback,
        dialog_manager,
        redeem_bonus_days,
        list_bonus_days_targets,
        get_translator(i18n, user),
        _redeemable_days(user),
    )


@inject
async def on_redeem_days_input(
    message: Message,
    widget: MessageInput,
    dialog_manager: DialogManager,
    redeem_bonus_days: FromDishka[RedeemBonusDaysUseCase],
    list_bonus_days_targets: FromDishka[ListBonusDaysTargetsUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    dialog_manager.show_mode = ShowMode.EDIT
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    runner = get_translator(i18n, user)
    text = (message.text or "").strip()
    limit = _redeemable_days(user)
    if not text.isdecimal() or not 0 < int(text) <= limit:
        await answer_notice(message, runner.get("ntf-referrals.invalid-days", max_days=limit))
        return
    await _redeem_days(
        message, dialog_manager, redeem_bonus_days, list_bonus_days_targets, runner, int(text)
    )


@inject
async def on_redeem_target_selected(
    callback: CallbackQuery,
    widget: Button,
    dialog_manager: DialogManager,
    redeem_bonus_days: FromDishka[RedeemBonusDaysUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    subscription_id = int(dialog_manager.item_id)  # type: ignore[attr-defined]
    await _redeem(
        callback, dialog_manager, redeem_bonus_days, get_translator(i18n, user), subscription_id
    )
