from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.enums import PromocodeLinkOutcome, PromocodeRewardType
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.promocodes.dto import (
    ActivatePromocodeByLinkDto,
    ActivatePromocodeDto,
    PromocodeDto,
)
from app.modules.promocodes.usecases import ActivatePromocodeByLinkUseCase

_USER = MagicMock(id=7, log="[user 7]")


def _promo(reward_type: PromocodeRewardType, *, is_reusable: bool = False) -> PromocodeDto:
    return PromocodeDto(
        id=1,
        code="LINK",
        is_active=True,
        reward_type=reward_type,
        reward=5,
        is_reusable=is_reusable,
    )


def _use_case(
    promo: PromocodeDto, targets: Optional[list[int]] = None
) -> tuple[ActivatePromocodeByLinkUseCase, AsyncMock]:
    activate = AsyncMock(return_value=promo)
    use_case = ActivatePromocodeByLinkUseCase(
        AsyncMock(return_value=promo),
        AsyncMock(return_value=[MagicMock(id=i) for i in targets or []]),
        activate,
    )
    return use_case, activate


@pytest.mark.parametrize(
    "reward_type",
    [
        PromocodeRewardType.PERSONAL_DISCOUNT,
        PromocodeRewardType.PURCHASE_DISCOUNT,
        PromocodeRewardType.SUBSCRIPTION,
    ],
)
async def test_a_reward_without_a_subscription_choice_is_applied_at_once(
    reward_type: PromocodeRewardType,
) -> None:
    use_case, activate = _use_case(_promo(reward_type))

    result = await use_case(_USER, ActivatePromocodeByLinkDto(code="link"))

    assert result.outcome is PromocodeLinkOutcome.ACTIVATED
    activate.assert_awaited_once_with(_USER, ActivatePromocodeDto(code="LINK"))


async def test_days_go_to_the_only_subscription_without_asking() -> None:
    use_case, activate = _use_case(_promo(PromocodeRewardType.DURATION), targets=[42])

    result = await use_case(_USER, ActivatePromocodeByLinkDto(code="LINK"))

    assert result.outcome is PromocodeLinkOutcome.ACTIVATED
    activate.assert_awaited_once_with(
        _USER, ActivatePromocodeDto(code="LINK", target_subscription_id=42)
    )


@pytest.mark.parametrize(
    ("targets", "outcome"),
    [([], PromocodeLinkOutcome.NO_TARGET), ([1, 2], PromocodeLinkOutcome.CHOOSE_TARGET)],
)
async def test_days_with_no_or_several_subscriptions_are_left_to_the_customer(
    targets: list[int], outcome: PromocodeLinkOutcome
) -> None:
    use_case, activate = _use_case(_promo(PromocodeRewardType.TRAFFIC), targets=targets)

    result = await use_case(_USER, ActivatePromocodeByLinkDto(code="LINK"))

    assert result.outcome is outcome
    assert result.promocode.code == "LINK"
    activate.assert_not_awaited()


async def test_a_refused_code_is_not_activated() -> None:
    activate = AsyncMock()
    refusal = ValidationError("used", code=ErrorCode.PROMOCODE_ALREADY_ACTIVATED)
    use_case = ActivatePromocodeByLinkUseCase(AsyncMock(side_effect=refusal), AsyncMock(), activate)

    with pytest.raises(ValidationError):
        await use_case(_USER, ActivatePromocodeByLinkDto(code="LINK"))
    activate.assert_not_awaited()


async def test_a_reusable_code_waits_for_the_customer_to_confirm() -> None:
    # opening the link again must not hand out the reward again
    use_case, activate = _use_case(
        _promo(PromocodeRewardType.DURATION, is_reusable=True), targets=[42]
    )

    result = await use_case(_USER, ActivatePromocodeByLinkDto(code="LINK"))

    assert result.outcome is PromocodeLinkOutcome.CONFIRM
    activate.assert_not_awaited()


async def test_a_reusable_code_with_nothing_to_apply_to_says_so() -> None:
    use_case, activate = _use_case(
        _promo(PromocodeRewardType.DEVICES, is_reusable=True), targets=[]
    )

    result = await use_case(_USER, ActivatePromocodeByLinkDto(code="LINK"))

    assert result.outcome is PromocodeLinkOutcome.NO_TARGET
    activate.assert_not_awaited()
