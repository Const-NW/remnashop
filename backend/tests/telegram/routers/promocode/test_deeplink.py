from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram_dialog import ShowMode, StartMode

from app.core.enums import AdLinkChannel, PromocodeLinkOutcome, PromocodeRewardType
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.advertising import ActivateAdLinkPromocodeDto
from app.modules.promocodes import (
    ActivatePromocodeByLinkDto,
    PromocodeDto,
    PromocodeLinkResultDto,
)
from app.telegram.routers.promocode import deeplink
from app.telegram.states import MainMenu, Promocode

_USER = MagicMock(id=5, log="[user 5]")
_MENU = {"state": MainMenu.MAIN, "mode": StartMode.RESET_STACK}
_REFUSAL = ValidationError("used", code=ErrorCode.PROMOCODE_ALREADY_ACTIVATED)


def _result(outcome: PromocodeLinkOutcome) -> PromocodeLinkResultDto:
    promo = PromocodeDto(
        id=1, code="GIFT", is_active=True, reward_type=PromocodeRewardType.DURATION, reward=7
    )
    return PromocodeLinkResultDto(outcome=outcome, promocode=promo)


@pytest.fixture
def notice(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    runner = MagicMock()
    runner.get.side_effect = lambda key, **kwargs: f"{key}{kwargs or ''}"
    monkeypatch.setattr(deeplink, "get_translator", lambda hub, user: runner)
    monkeypatch.setattr(deeplink, "hit_counter", AsyncMock(return_value=1))
    mock = AsyncMock()
    monkeypatch.setattr(deeplink, "answer_notice", mock)
    return mock


def _manager(calls: list[str]) -> AsyncMock:
    manager = AsyncMock()
    manager.start.side_effect = lambda *args, **kwargs: calls.append("menu")
    return manager


def _redis(attempts: int = 0) -> MagicMock:
    redis = MagicMock()
    redis.get = AsyncMock(return_value=str(attempts).encode() if attempts else None)
    return redis


async def _open_ad_link(
    activate: AsyncMock, manager: AsyncMock, redis: Optional[MagicMock] = None
) -> None:
    await deeplink.on_ad_link(
        MagicMock(),
        MagicMock(args="ad_spring"),
        _USER,
        manager,
        activate=activate,
        validate_promocode=AsyncMock(),
        list_promocode_targets=AsyncMock(),
        redis=redis or _redis(),
        i18n=MagicMock(),
    )


async def _open_promocode_link(activate: AsyncMock, manager: AsyncMock) -> None:
    await deeplink.on_promocode_link(
        MagicMock(),
        MagicMock(args="promo_spring10"),
        _USER,
        manager,
        activate=activate,
        validate_promocode=AsyncMock(),
        list_promocode_targets=AsyncMock(),
        redis=MagicMock(),
        i18n=MagicMock(),
    )


async def test_an_ad_link_without_a_promocode_only_opens_the_menu(notice: AsyncMock) -> None:
    activate = AsyncMock(return_value=None)
    manager = AsyncMock()

    await _open_ad_link(activate, manager)

    activate.assert_awaited_once_with(
        _USER, ActivateAdLinkPromocodeDto(ad_link_code="spring", channel=AdLinkChannel.BOT)
    )
    manager.start.assert_awaited_once_with(**_MENU, show_mode=ShowMode.DELETE_AND_SEND)
    notice.assert_not_awaited()


@pytest.mark.parametrize(
    ("outcome", "text"),
    [
        (PromocodeLinkOutcome.ACTIVATED, "ntf-promocode.activated"),
        (PromocodeLinkOutcome.NO_TARGET, "ntf-promocode.link-no-target{'code': 'GIFT'}"),
    ],
)
async def test_the_menu_is_drawn_after_the_promocode_is_applied(
    outcome: PromocodeLinkOutcome, text: str, notice: AsyncMock
) -> None:
    calls: list[str] = []

    async def apply(*args: object) -> PromocodeLinkResultDto:
        calls.append("apply")
        return _result(outcome)

    await _open_ad_link(AsyncMock(side_effect=apply), _manager(calls))

    assert calls == ["apply", "menu"]
    assert notice.await_args is not None
    assert notice.await_args.args[1] == text


async def test_an_ad_link_keeps_quiet_about_a_refused_promocode(notice: AsyncMock) -> None:
    manager = AsyncMock()

    await _open_ad_link(AsyncMock(side_effect=_REFUSAL), manager)

    manager.start.assert_awaited_once_with(**_MENU, show_mode=ShowMode.DELETE_AND_SEND)
    notice.assert_not_awaited()


async def test_a_promocode_link_says_why_it_was_refused(notice: AsyncMock) -> None:
    await _open_promocode_link(AsyncMock(side_effect=_REFUSAL), AsyncMock())

    assert notice.await_args is not None
    assert notice.await_args.args[1] == "ntf-promocode.already-activated"


@pytest.mark.parametrize(
    "outcome", [PromocodeLinkOutcome.CHOOSE_TARGET, PromocodeLinkOutcome.CONFIRM]
)
async def test_a_step_left_to_the_customer_draws_the_promocode_dialog_once(
    outcome: PromocodeLinkOutcome, monkeypatch: pytest.MonkeyPatch, notice: AsyncMock
) -> None:
    proceed = AsyncMock()
    monkeypatch.setattr(deeplink, "continue_with_code", proceed)
    manager = AsyncMock()
    shown_with: list[object] = []
    # show() draws by the manager's own mode, not by an argument
    manager.show.side_effect = lambda *args: shown_with.append(manager.show_mode)

    await _open_ad_link(AsyncMock(return_value=_result(outcome)), manager)

    # nothing drawn until the dialog stands on the step continue_with_code chose
    assert [call.kwargs["show_mode"] for call in manager.start.await_args_list] == [
        ShowMode.NO_UPDATE,
        ShowMode.NO_UPDATE,
    ]
    assert manager.start.await_args_list[1].args == (Promocode.INPUT,)
    assert proceed.await_args is not None
    assert proceed.await_args.args[:3] == ("GIFT", _USER, manager)
    manager.show.assert_awaited_once_with()
    assert shown_with == [ShowMode.DELETE_AND_SEND]


async def test_a_promocode_link_activates_its_code_uppercased(notice: AsyncMock) -> None:
    activate = AsyncMock(return_value=_result(PromocodeLinkOutcome.ACTIVATED))

    await _open_promocode_link(activate, AsyncMock())

    activate.assert_awaited_once_with(_USER, ActivatePromocodeByLinkDto(code="SPRING10"))


async def test_a_promocode_link_over_the_limit_is_refused(
    monkeypatch: pytest.MonkeyPatch, notice: AsyncMock
) -> None:
    monkeypatch.setattr(
        deeplink, "hit_counter", AsyncMock(return_value=deeplink.PROMOCODE_INPUT_LIMIT + 1)
    )
    activate = AsyncMock()

    await _open_promocode_link(activate, AsyncMock())

    activate.assert_not_awaited()
    assert notice.await_args is not None
    assert notice.await_args.args[1] == "ntf-promocode.too-many-attempts"


async def test_an_ad_link_counts_an_attempt_only_when_it_carries_a_promocode(
    monkeypatch: pytest.MonkeyPatch, notice: AsyncMock
) -> None:
    counter = AsyncMock(return_value=1)
    monkeypatch.setattr(deeplink, "hit_counter", counter)

    await _open_ad_link(AsyncMock(return_value=None), AsyncMock())
    assert counter.await_count == 0

    await _open_ad_link(
        AsyncMock(return_value=_result(PromocodeLinkOutcome.ACTIVATED)), AsyncMock()
    )
    assert counter.await_count == 1


async def test_an_ad_link_over_the_limit_only_opens_the_menu(notice: AsyncMock) -> None:
    activate, manager = AsyncMock(), AsyncMock()

    await _open_ad_link(activate, manager, _redis(attempts=deeplink.PROMOCODE_INPUT_LIMIT))

    activate.assert_not_awaited()
    manager.start.assert_awaited_once_with(**_MENU, show_mode=ShowMode.DELETE_AND_SEND)
    notice.assert_not_awaited()
