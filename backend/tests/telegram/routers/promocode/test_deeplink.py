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
    mock = AsyncMock()
    monkeypatch.setattr(deeplink, "answer_notice", mock)
    return mock


async def _open_ad_link(activate: AsyncMock) -> AsyncMock:
    manager = AsyncMock()
    await deeplink.on_ad_link(
        MagicMock(),
        MagicMock(args="ad_spring"),
        _USER,
        manager,
        activate=activate,
        validate_promocode=AsyncMock(),
        list_promocode_targets=AsyncMock(),
        i18n=MagicMock(),
    )
    return manager


async def test_an_ad_link_without_a_promocode_only_opens_the_menu(notice: AsyncMock) -> None:
    activate = AsyncMock(return_value=None)

    manager = await _open_ad_link(activate)

    activate.assert_awaited_once_with(
        _USER, ActivateAdLinkPromocodeDto(ad_link_code="spring", channel=AdLinkChannel.BOT)
    )
    manager.start.assert_awaited_once_with(
        state=MainMenu.MAIN, mode=StartMode.RESET_STACK, show_mode=ShowMode.DELETE_AND_SEND
    )
    notice.assert_not_awaited()


@pytest.mark.parametrize(
    ("outcome", "text"),
    [
        (PromocodeLinkOutcome.ACTIVATED, "ntf-promocode.activated"),
        (PromocodeLinkOutcome.NO_TARGET, "ntf-promocode.link-no-target{'code': 'GIFT'}"),
    ],
)
async def test_an_ad_link_says_what_its_promocode_did(
    outcome: PromocodeLinkOutcome, text: str, notice: AsyncMock
) -> None:
    await _open_ad_link(AsyncMock(return_value=_result(outcome)))

    assert notice.await_args is not None
    assert notice.await_args.args[1] == text


async def test_a_refused_promocode_says_why(notice: AsyncMock) -> None:
    refusal = ValidationError("used", code=ErrorCode.PROMOCODE_ALREADY_ACTIVATED)

    await _open_ad_link(AsyncMock(side_effect=refusal))

    assert notice.await_args is not None
    assert notice.await_args.args[1] == "ntf-promocode.already-activated"


async def test_several_subscriptions_open_the_promocode_dialog_on_the_choice(
    monkeypatch: pytest.MonkeyPatch, notice: AsyncMock
) -> None:
    proceed = AsyncMock()
    monkeypatch.setattr(deeplink, "continue_with_code", proceed)

    manager = await _open_ad_link(
        AsyncMock(return_value=_result(PromocodeLinkOutcome.CHOOSE_TARGET))
    )

    assert manager.start.await_args_list[-1].args == (Promocode.INPUT,)
    assert proceed.await_args is not None
    assert proceed.await_args.args[:3] == ("GIFT", _USER, manager)


async def _open_promocode_link(
    monkeypatch: pytest.MonkeyPatch, activate: AsyncMock, attempts: int
) -> None:
    monkeypatch.setattr(deeplink, "hit_counter", AsyncMock(return_value=attempts))
    await deeplink.on_promocode_link(
        MagicMock(),
        MagicMock(args="promo_spring10"),
        _USER,
        AsyncMock(),
        activate=activate,
        validate_promocode=AsyncMock(),
        list_promocode_targets=AsyncMock(),
        redis=MagicMock(),
        i18n=MagicMock(),
    )


async def test_a_promocode_link_activates_its_code_uppercased(
    monkeypatch: pytest.MonkeyPatch, notice: AsyncMock
) -> None:
    activate = AsyncMock(return_value=_result(PromocodeLinkOutcome.ACTIVATED))

    await _open_promocode_link(monkeypatch, activate, attempts=1)

    activate.assert_awaited_once_with(_USER, ActivatePromocodeByLinkDto(code="SPRING10"))


async def test_a_promocode_link_counts_as_a_typed_guess(
    monkeypatch: pytest.MonkeyPatch, notice: AsyncMock
) -> None:
    activate = AsyncMock()

    await _open_promocode_link(monkeypatch, activate, attempts=deeplink.PROMOCODE_INPUT_LIMIT + 1)

    activate.assert_not_awaited()
    assert notice.await_args is not None
    assert notice.await_args.args[1] == "ntf-promocode.too-many-attempts"
