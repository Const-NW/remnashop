from typing import Final

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram_dialog import DialogManager, ShowMode, StartMode
from dishka import FromDishka
from fluentogram import TranslatorHub

from app.core.enums import PurchaseType
from app.modules.referrals import GetReferralSettingsUseCase
from app.modules.settings import GetShopSettingsUseCase
from app.modules.subscriptions import (
    ListOwnActiveSubscriptionsUseCase,
    ListOwnSubscriptionsUseCase,
)
from app.modules.users import UserDto
from app.telegram.dialogs.context import menu_flags
from app.telegram.i18n import get_translator
from app.telegram.notice import answer_notice
from app.telegram.routers.subscriptions.handlers import open_devices, open_subscriptions
from app.telegram.states import MainMenu, Purchase

# t.me/<bot>?start=<section>: what the menu's own buttons open, for posts and support replies
_BUY: Final[str] = "buy"
_SUBSCRIPTION: Final[str] = "sub"
_DEVICES: Final[str] = "devices"

router = Router(name=__name__)


@router.message(CommandStart(deep_link=True, magic=F.args == _BUY))
async def on_buy_link(
    message: Message,
    user: UserDto,
    dialog_manager: DialogManager,
    list_own_subscriptions: FromDishka[ListOwnSubscriptionsUseCase],
    get_shop_settings: FromDishka[GetShopSettingsUseCase],
    get_referral_settings: FromDishka[GetReferralSettingsUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    owned = await list_own_subscriptions(user)
    can_purchase, _, _ = await menu_flags(owned, get_shop_settings, get_referral_settings)
    if not can_purchase:
        # at the cap a new one is refused at the last step: renewing or changing is what remains
        dialog_manager.show_mode = ShowMode.DELETE_AND_SEND
        await open_subscriptions(dialog_manager, owned)
        await answer_notice(message, get_translator(i18n, user).get("ntf-purchase.limit-reached"))
        return
    # over the menu, so "Back" lands there as from the menu's own button
    await dialog_manager.start(
        MainMenu.MAIN, mode=StartMode.RESET_STACK, show_mode=ShowMode.NO_UPDATE
    )
    await dialog_manager.start(
        Purchase.PLAN,
        data={"purchase_type": PurchaseType.NEW.value},
        show_mode=ShowMode.DELETE_AND_SEND,
    )


@router.message(CommandStart(deep_link=True, magic=F.args == _SUBSCRIPTION))
async def on_subscription_link(
    message: Message,
    user: UserDto,
    dialog_manager: DialogManager,
    list_own_subscriptions: FromDishka[ListOwnSubscriptionsUseCase],
) -> None:
    dialog_manager.show_mode = ShowMode.DELETE_AND_SEND
    await open_subscriptions(dialog_manager, await list_own_subscriptions(user))


@router.message(CommandStart(deep_link=True, magic=F.args == _DEVICES))
async def on_devices_link(
    message: Message,
    user: UserDto,
    dialog_manager: DialogManager,
    list_own_active_subscriptions: FromDishka[ListOwnActiveSubscriptionsUseCase],
    list_own_subscriptions: FromDishka[ListOwnSubscriptionsUseCase],
    get_shop_settings: FromDishka[GetShopSettingsUseCase],
) -> None:
    dialog_manager.show_mode = ShowMode.DELETE_AND_SEND
    active = await list_own_active_subscriptions(user)
    single = (await get_shop_settings.system()).max_active_subscriptions == 1
    # where the menu has a "Devices" button: one subscription per customer, with a device limit;
    # otherwise each subscription's devices sit on its card
    if single and active and active[0].effective_device_limit != 0:
        await open_devices(dialog_manager, active)
    else:
        await open_subscriptions(dialog_manager, await list_own_subscriptions(user))
