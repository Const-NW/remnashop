from typing import Optional

from app.core.db.uow import UnitOfWork
from app.core.exceptions import NotFoundError, ValidationError
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.advertising.dto import ActivateAdLinkPromocodeDto
from app.modules.advertising.repository import AdLinkRepository
from app.modules.promocodes import (
    ActivatePromocodeByLinkDto,
    ActivatePromocodeByLinkUseCase,
    PromocodeLinkResultDto,
    PromocodeRepository,
)
from app.modules.users import UserDto


class ActivateAdLinkPromocodeUseCase(
    UseCase[ActivateAdLinkPromocodeDto, Optional[PromocodeLinkResultDto], UserDto]
):
    """The promocode of the ad link a customer came by, applied as a promocode
    link would apply it; None when the link carries none it may give."""

    required_permission = Permission.PUBLIC

    def __init__(
        self,
        ad_link_repository: AdLinkRepository,
        promocode_repository: PromocodeRepository,
        uow: UnitOfWork,
        activate_promocode_by_link_use_case: ActivatePromocodeByLinkUseCase,
    ) -> None:
        self._ad_link_repository = ad_link_repository
        self._promocode_repository = promocode_repository
        self._uow = uow
        self._activate_promocode_by_link_use_case = activate_promocode_by_link_use_case

    async def _execute(
        self, actor: UserDto, data: ActivateAdLinkPromocodeDto
    ) -> Optional[PromocodeLinkResultDto]:
        async with self._uow:
            ad_link = await self._ad_link_repository.find_by_code(data.ad_link_code)
            if (
                ad_link is None
                or not ad_link.is_active
                or not ad_link.allows(data.channel)
                or ad_link.promocode_id is None
            ):
                return None
            promo = await self._promocode_repository.get_by_id(ad_link.promocode_id)
        try:
            return await self._activate_promocode_by_link_use_case(
                actor, ActivatePromocodeByLinkDto(code=promo.code)
            )
        except (NotFoundError, ValidationError) as error:
            # the detail names the code, which the ad link keeps to itself
            raise type(error)("The ad link's promocode can't be applied", code=error.code) from None
