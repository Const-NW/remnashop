from loguru import logger

from app.core.enums import PromocodeLinkOutcome
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.promocodes.dto import (
    ActivatePromocodeByLinkDto,
    ActivatePromocodeDto,
    ListPromocodeTargetsDto,
    PromocodeLinkResultDto,
    ValidatePromocodeDto,
)
from app.modules.promocodes.plan_limits import PLAN_LIMITED_REWARDS
from app.modules.promocodes.usecases.activate_promocode import ActivatePromocodeUseCase
from app.modules.promocodes.usecases.list_promocode_targets import ListPromocodeTargetsUseCase
from app.modules.promocodes.usecases.validate_promocode import ValidatePromocodeUseCase
from app.modules.users import UserDto


class ActivatePromocodeByLinkUseCase(
    UseCase[ActivatePromocodeByLinkDto, PromocodeLinkResultDto, UserDto]
):
    """A promocode opened by a link: applied without asking when it needs no
    choice, otherwise the customer is told what's missing."""

    required_permission = Permission.PUBLIC

    def __init__(
        self,
        validate_promocode_use_case: ValidatePromocodeUseCase,
        list_promocode_targets_use_case: ListPromocodeTargetsUseCase,
        activate_promocode_use_case: ActivatePromocodeUseCase,
    ) -> None:
        self._validate_promocode_use_case = validate_promocode_use_case
        self._list_promocode_targets_use_case = list_promocode_targets_use_case
        self._activate_promocode_use_case = activate_promocode_use_case

    async def _execute(
        self, actor: UserDto, data: ActivatePromocodeByLinkDto
    ) -> PromocodeLinkResultDto:
        promo = await self._validate_promocode_use_case(actor, ValidatePromocodeDto(code=data.code))
        target_id = None
        if promo.reward_type in PLAN_LIMITED_REWARDS:
            targets = await self._list_promocode_targets_use_case(
                actor, ListPromocodeTargetsDto(code=promo.code)
            )
            if not targets:
                return PromocodeLinkResultDto(
                    outcome=PromocodeLinkOutcome.NO_TARGET, promocode=promo
                )
            if len(targets) > 1:
                return PromocodeLinkResultDto(
                    outcome=PromocodeLinkOutcome.CHOOSE_TARGET, promocode=promo
                )
            target_id = targets[0].id
        if promo.is_reusable:
            # opening the link again must never repeat the reward on its own
            return PromocodeLinkResultDto(outcome=PromocodeLinkOutcome.CONFIRM, promocode=promo)

        activated = await self._activate_promocode_use_case(
            actor, ActivatePromocodeDto(code=promo.code, target_subscription_id=target_id)
        )
        logger.info(f"{actor.log} Activated promocode '{promo.code}' by link")
        return PromocodeLinkResultDto(outcome=PromocodeLinkOutcome.ACTIVATED, promocode=activated)
