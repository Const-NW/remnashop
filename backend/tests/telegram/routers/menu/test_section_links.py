from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram_dialog import ShowMode, StartMode

from app.core.enums import PurchaseType
from app.telegram.routers.menu import section_links
from app.telegram.states import MainMenu, Purchase

_USER = MagicMock(id=9)


def _settings(max_active: Optional[int]) -> tuple[MagicMock, MagicMock]:
    shop = MagicMock()
    shop.system = AsyncMock(return_value=MagicMock(max_active_subscriptions=max_active))
    referral = MagicMock()
    referral.system = AsyncMock(return_value=MagicMock(enabled=False))
    return shop, referral


async def _open_buy(
    monkeypatch: pytest.MonkeyPatch, *, max_active: Optional[int], owned: int
) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    notice, opened = AsyncMock(), AsyncMock()
    monkeypatch.setattr(section_links, "answer_notice", notice)
    monkeypatch.setattr(section_links, "open_subscriptions", opened)
    runner = MagicMock()
    runner.get.side_effect = lambda key: key
    monkeypatch.setattr(section_links, "get_translator", lambda hub, user: runner)
    shop, referral = _settings(max_active)
    manager = AsyncMock()
    await section_links.on_buy_link(
        MagicMock(),
        _USER,
        manager,
        list_own_subscriptions=AsyncMock(
            return_value=[MagicMock(is_trial=False) for _ in range(owned)]
        ),
        get_shop_settings=shop,
        get_referral_settings=referral,
        i18n=MagicMock(),
    )
    return manager, notice, opened


async def test_buy_opens_the_purchase_over_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, notice, _ = await _open_buy(monkeypatch, max_active=3, owned=1)

    # the menu below is started without drawing, so only the purchase shows
    assert [call.args for call in manager.start.await_args_list] == [
        (MainMenu.MAIN,),
        (Purchase.PLAN,),
    ]
    menu, purchase = manager.start.await_args_list
    assert menu.kwargs == {"mode": StartMode.RESET_STACK, "show_mode": ShowMode.NO_UPDATE}
    assert purchase.kwargs == {
        "data": {"purchase_type": PurchaseType.NEW.value},
        "show_mode": ShowMode.DELETE_AND_SEND,
    }
    notice.assert_not_awaited()


async def test_buy_at_the_cap_opens_the_subscriptions_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, notice, opened = await _open_buy(monkeypatch, max_active=1, owned=1)

    manager.start.assert_not_awaited()
    opened.assert_awaited_once()
    assert notice.await_args is not None
    assert notice.await_args.args[1] == "ntf-purchase.limit-reached"


async def test_sub_opens_what_my_subscriptions_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = AsyncMock()
    monkeypatch.setattr(section_links, "open_subscriptions", opened)
    owned = [MagicMock()]
    manager = AsyncMock()

    await section_links.on_subscription_link(
        MagicMock(), _USER, manager, list_own_subscriptions=AsyncMock(return_value=owned)
    )

    opened.assert_awaited_once_with(manager, owned)
    assert manager.show_mode is ShowMode.DELETE_AND_SEND


@pytest.mark.parametrize(
    ("max_active", "device_limit", "opens_devices"),
    [(1, 3, True), (1, 0, False), (3, 3, False)],
    ids=["menu-has-devices", "unlimited-devices", "several-subscriptions"],
)
async def test_devices_open_only_where_the_menu_shows_them(
    max_active: int, device_limit: int, opens_devices: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened_subscriptions, opened_devices = AsyncMock(), AsyncMock()
    monkeypatch.setattr(section_links, "open_subscriptions", opened_subscriptions)
    monkeypatch.setattr(section_links, "open_devices", opened_devices)
    active = [MagicMock(effective_device_limit=device_limit)]
    shop, _ = _settings(max_active)

    await section_links.on_devices_link(
        MagicMock(),
        _USER,
        AsyncMock(),
        list_own_active_subscriptions=AsyncMock(return_value=active),
        list_own_subscriptions=AsyncMock(return_value=active),
        get_shop_settings=shop,
    )

    assert opened_devices.await_count == int(opens_devices)
    assert opened_subscriptions.await_count == int(not opens_devices)


@pytest.mark.parametrize(
    ("payload", "handler"),
    [("buy", "on_buy_link"), ("sub", "on_subscription_link"), ("devices", "on_devices_link")],
)
def test_each_section_answers_its_own_payload_only(payload: str, handler: str) -> None:
    observer = section_links.router.message
    filters = {h.callback.__name__: h.filters for h in observer.handlers}
    assert handler in filters
    # the magic filter compares the whole payload: "buy" is not "buyer"
    magic = filters[handler][0].callback.magic
    assert magic.resolve(MagicMock(args=payload))
    assert not magic.resolve(MagicMock(args=payload + "x"))
