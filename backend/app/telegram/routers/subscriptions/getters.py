from typing import Any, Awaitable, Callable, Final, Optional, TypeAlias

from aiogram_dialog import DialogManager
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from fluentogram import TranslatorHub, TranslatorRunner

from app.core.config import AppConfig
from app.core.constants import DEVICE_NAME_MAX_LENGTH, USER_KEY
from app.core.enums import SubscriptionStatus
from app.core.exceptions import NotFoundError, ValidationError
from app.modules.banners import GetBannerForDisplayUseCase
from app.modules.referrals import GetReferralSettingsUseCase
from app.modules.settings import GetCabinetAuthSettingsUseCase, GetShopSettingsUseCase
from app.modules.subscriptions import (
    DeviceDto,
    GetAvailableTrialUseCase,
    ListDevicesUseCase,
    ListOwnSubscriptionsUseCase,
    SubscriptionDto,
    SubscriptionRefDto,
    is_unlimited_expiry,
)
from app.modules.users import UserDto
from app.telegram.banners import window_banner
from app.telegram.dialogs.config_cache import DialogConfigCache
from app.telegram.dialogs.context import build_condition_data, menu_flags, selected_subscription_id
from app.telegram.dialogs.rendering import (
    UNAVAILABLE_BUTTONS_KEY,
    ButtonLabel,
    label_with_icon,
    plan_title,
    render_dialog_buttons,
    render_window_text,
    resolve_variable_kwargs,
)
from app.telegram.i18n import (
    expiry_unix_time,
    format_date,
    format_date_tag,
    format_expiry,
    format_expiry_tag,
    get_translator,
    unix_time,
)
from app.telegram.routers.subscriptions.handlers import SELECTED_HWID_KEY

# the open device card's title and whether the customer named it, for the rename window
DEVICE_TITLE_KEY: Final[str] = "device_title"
DEVICE_HAS_NAME_KEY: Final[str] = "device_has_name"

_KNOWN_PLATFORMS: Final[frozenset[str]] = frozenset({"ios", "android", "windows", "macos", "linux"})


def _platform_icon_key(platform: Optional[str]) -> str:
    normalized = (platform or "default").lower()
    return normalized if normalized in _KNOWN_PLATFORMS else "default"


def grace_line(runner: TranslatorRunner, subscription: SubscriptionDto) -> str:
    """The card's grace-period line; empty (so the line drops) outside a grace."""
    if subscription.grace_until is None:
        return ""
    return runner.get(
        "frg-subscription-grace",
        grace_until="none"
        if is_unlimited_expiry(subscription.grace_until)
        else format_date(subscription.grace_until),
    )


def device_title(device: DeviceDto) -> str:
    return device.name or device.device_model or device.platform or device.hwid[:8]


def _subscription_label(
    runner: TranslatorRunner, subscription: SubscriptionDto, user: UserDto
) -> ButtonLabel:
    snapshot = subscription.plan_snapshot
    name = snapshot.display_name(user.language)
    return label_with_icon(
        runner,
        "btn-subscriptions.item",
        default_icon_id=snapshot.icon_emoji_id,
        # an active unlimited one has no date to read "until"
        status="UNLIMITED"
        if subscription.status is SubscriptionStatus.ACTIVE and subscription.is_unlimited
        else subscription.status.value,
        # a custom emoji goes into the button's icon; a bare glyph has nowhere else to go
        plan_name=f"{snapshot.icon_emoji_fallback} {name}"
        if snapshot.icon_emoji_fallback and not snapshot.icon_emoji_id
        else name,
        expire_at=format_expiry(runner, subscription.expire_at),
    )


@inject
async def subscriptions_list_getter(
    dialog_manager: DialogManager,
    config: FromDishka[AppConfig],
    get_banner: FromDishka[GetBannerForDisplayUseCase],
    list_own_subscriptions: FromDishka[ListOwnSubscriptionsUseCase],
    get_available_trial: FromDishka[GetAvailableTrialUseCase],
    get_cabinet_auth_settings: FromDishka[GetCabinetAuthSettingsUseCase],
    get_shop_settings: FromDishka[GetShopSettingsUseCase],
    get_referral_settings: FromDishka[GetReferralSettingsUseCase],
    i18n: FromDishka[TranslatorHub],
    dialog_config_cache: FromDishka[DialogConfigCache],
    **kwargs: Any,
) -> dict[str, Any]:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    runner = get_translator(i18n, user)
    subscriptions = await list_own_subscriptions(user)
    active = [s for s in subscriptions if s.status is not SubscriptionStatus.EXPIRED]
    can_purchase, referral_enabled, single_subscription = await menu_flags(
        subscriptions, get_shop_settings, get_referral_settings
    )
    data: dict[str, Any] = await build_condition_data(
        user,
        active,
        get_available_trial,
        get_cabinet_auth_settings,
        cabinet_url=config.cabinet.url,
        can_purchase=can_purchase,
        referral_enabled=referral_enabled,
        single_subscription=single_subscription,
        owned_count=len(subscriptions),
    )
    data["banner"] = await window_banner(
        "subscriptions.list", dialog_config_cache, get_banner, config, user
    )
    data["subscriptions"] = [
        {
            "id": s.id,
            "label": _subscription_label(runner, s, user),
        }
        for s in subscriptions
    ]
    data["window_text"] = render_window_text(
        runner, "msg-subscriptions-list", **resolve_variable_kwargs(data)
    )
    data["buttons"] = await render_dialog_buttons(
        "subscriptions.list", dialog_config_cache, runner, data
    )
    return data


@inject
async def subscription_card_getter(
    dialog_manager: DialogManager,
    config: FromDishka[AppConfig],
    get_banner: FromDishka[GetBannerForDisplayUseCase],
    list_own_subscriptions: FromDishka[ListOwnSubscriptionsUseCase],
    get_available_trial: FromDishka[GetAvailableTrialUseCase],
    get_cabinet_auth_settings: FromDishka[GetCabinetAuthSettingsUseCase],
    i18n: FromDishka[TranslatorHub],
    dialog_config_cache: FromDishka[DialogConfigCache],
    **kwargs: Any,
) -> dict[str, Any]:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    runner = get_translator(i18n, user)
    data: dict[str, Any] = await build_condition_data(
        user,
        await list_own_subscriptions(user),
        get_available_trial,
        get_cabinet_auth_settings,
        cabinet_url=config.cabinet.url,
        selected_id=selected_subscription_id(dialog_manager),
    )
    data["banner"] = await window_banner(
        "subscriptions.card", dialog_config_cache, get_banner, config, user
    )
    subscription: Optional[SubscriptionDto] = data["subscription"]
    if subscription is None:
        # expired or deleted while the card stayed open
        data["window_text"] = render_window_text(runner, "msg-window-gone")
    else:
        data["window_text"] = render_window_text(
            runner,
            "msg-subscription-card",
            plan_name=plan_title(
                subscription.plan_snapshot.display_name(user.language),
                subscription.plan_snapshot.icon_emoji_id,
                subscription.plan_snapshot.icon_emoji_fallback,
            ),
            status=subscription.status.value,
            is_trial=int(subscription.is_trial),
            expire_at=format_expiry(runner, subscription.expire_at),
            expire_at_local=format_expiry_tag(runner, subscription.expire_at),
            expire_at_unix=expiry_unix_time(subscription.expire_at),
            is_unlimited=int(subscription.is_unlimited),
            traffic_limit=subscription.effective_traffic_limit,
            device_limit=subscription.effective_device_limit,
            grace=grace_line(runner, subscription),
            **resolve_variable_kwargs(data),
        )
    data["buttons"] = await render_dialog_buttons(
        "subscriptions.card", dialog_config_cache, runner, data
    )
    return data


@inject
async def devices_getter(
    dialog_manager: DialogManager,
    list_own_subscriptions: FromDishka[ListOwnSubscriptionsUseCase],
    list_devices: FromDishka[ListDevicesUseCase],
    get_shop_settings: FromDishka[GetShopSettingsUseCase],
    i18n: FromDishka[TranslatorHub],
    dialog_config_cache: FromDishka[DialogConfigCache],
    **kwargs: Any,
) -> dict[str, Any]:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    runner = get_translator(i18n, user)
    shop = await get_shop_settings.system()
    unavailable: set[str] = set()
    if not shop.device_all_reset_enabled:
        unavailable |= {"menu.device_confirm_delete_all", "delete_all_devices"}
    if not shop.link_reset_enabled:
        unavailable |= {"menu.device_confirm_reissue", "reissue_subscription"}

    subscription_id = selected_subscription_id(dialog_manager)
    subscription = next(
        (s for s in await list_own_subscriptions(user) if s.id == subscription_id), None
    )
    devices: list[DeviceDto] = []
    if subscription is not None:
        try:
            devices = await list_devices(user, SubscriptionRefDto(subscription_id=subscription.id))
        except (NotFoundError, ValidationError):
            subscription = None

    formatted: list[dict[str, Any]] = [
        {
            "index": index,
            "hwid": device.hwid,
            "label": label_with_icon(
                runner,
                "btn-devices.item",
                platform_icon=runner.get(f"platform-icon.{_platform_icon_key(device.platform)}"),
                device_name=device_title(device),
                platform=device.platform or "?",
                device_model=device.device_model or "?",
                created_at=format_date(device.created_at),
            ),
        }
        for index, device in enumerate(devices)
    ]
    dialog_manager.dialog_data["hwid_map"] = [{"hwid": item["hwid"]} for item in formatted]

    data: dict[str, Any] = {
        "devices": formatted,
        "has_devices": len(formatted) > 0,
        "window_text": (
            render_window_text(
                runner,
                "msg-menu-devices",
                devices_current=len(formatted),
                devices_max=subscription.effective_device_limit,
                **resolve_variable_kwargs({}),
            )
            if subscription is not None
            # expired or deleted while the window stayed open; the action
            # handlers send the customer back to the subscriptions
            else render_window_text(runner, "msg-window-gone")
        ),
        UNAVAILABLE_BUTTONS_KEY: unavailable,
    }
    data["buttons"] = await render_dialog_buttons("menu.devices", dialog_config_cache, runner, data)
    return data


@inject
async def device_getter(
    dialog_manager: DialogManager,
    list_devices: FromDishka[ListDevicesUseCase],
    get_shop_settings: FromDishka[GetShopSettingsUseCase],
    i18n: FromDishka[TranslatorHub],
    dialog_config_cache: FromDishka[DialogConfigCache],
    **kwargs: Any,
) -> dict[str, Any]:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    runner = get_translator(i18n, user)
    subscription_id = selected_subscription_id(dialog_manager)
    hwid = dialog_manager.dialog_data.get(SELECTED_HWID_KEY)
    device: Optional[DeviceDto] = None
    if subscription_id is not None and hwid:
        try:
            devices = await list_devices(user, SubscriptionRefDto(subscription_id=subscription_id))
        except (NotFoundError, ValidationError):
            devices = []
        device = next((d for d in devices if d.hwid == hwid), None)

    unavailable: set[str] = set()
    if device is None:
        # removed while the window stayed open: only "back" is left
        unavailable |= {"menu.device_rename", "menu.device_confirm_delete"}
        window_text = render_window_text(runner, "msg-window-gone")
    else:
        if not (await get_shop_settings.system()).device_single_reset_enabled:
            unavailable.add("menu.device_confirm_delete")
        dialog_manager.dialog_data[DEVICE_TITLE_KEY] = device_title(device)
        dialog_manager.dialog_data[DEVICE_HAS_NAME_KEY] = device.name is not None
        window_text = render_window_text(
            runner,
            "msg-menu-device",
            device_name=device_title(device),
            # the title already is the model while the customer gave no name
            device_model=device.device_model if device.name else "",
            device_system=" ".join(filter(None, (device.platform, device.os_version))),
            device_app=device.app or "",
            device_created_at=format_date(device.created_at),
            device_created_at_local=format_date_tag(device.created_at),
            device_created_at_unix=unix_time(device.created_at),
            **resolve_variable_kwargs({}),
        )
    data: dict[str, Any] = {
        "window_text": window_text,
        "device_has_name": device is not None and device.name is not None,
        UNAVAILABLE_BUTTONS_KEY: unavailable,
    }
    data["buttons"] = await render_dialog_buttons("menu.device", dialog_config_cache, runner, data)
    return data


@inject
async def device_rename_getter(
    dialog_manager: DialogManager,
    i18n: FromDishka[TranslatorHub],
    dialog_config_cache: FromDishka[DialogConfigCache],
    **kwargs: Any,
) -> dict[str, Any]:
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    runner = get_translator(i18n, user)
    data: dict[str, Any] = {
        "window_text": render_window_text(
            runner,
            "msg-menu-device-rename",
            device_name=dialog_manager.dialog_data.get(DEVICE_TITLE_KEY, ""),
            device_name_max_length=DEVICE_NAME_MAX_LENGTH,
            **resolve_variable_kwargs({}),
        ),
        "device_has_name": bool(dialog_manager.dialog_data.get(DEVICE_HAS_NAME_KEY)),
    }
    data["buttons"] = await render_dialog_buttons(
        "menu.device_rename", dialog_config_cache, runner, data
    )
    return data


_Getter: TypeAlias = Callable[..., Awaitable[dict[str, Any]]]


def _confirm_getter(text_key: str, window_key: str) -> _Getter:
    @inject
    async def getter(
        dialog_manager: DialogManager,
        i18n: FromDishka[TranslatorHub],
        dialog_config_cache: FromDishka[DialogConfigCache],
        **kwargs: Any,
    ) -> dict[str, Any]:
        user: UserDto = dialog_manager.middleware_data[USER_KEY]
        runner = get_translator(i18n, user)
        data: dict[str, Any] = {
            "window_text": render_window_text(runner, text_key, **resolve_variable_kwargs({}))
        }
        data["buttons"] = await render_dialog_buttons(window_key, dialog_config_cache, runner, data)
        return data

    return getter


device_confirm_delete_getter: Final[_Getter] = _confirm_getter(
    "msg-menu-devices-confirm-delete", "menu.device_confirm_delete"
)
device_confirm_delete_all_getter: Final[_Getter] = _confirm_getter(
    "msg-menu-devices-confirm-delete-all", "menu.device_confirm_delete_all"
)
device_confirm_reissue_getter: Final[_Getter] = _confirm_getter(
    "msg-menu-devices-confirm-reissue", "menu.device_confirm_reissue"
)
