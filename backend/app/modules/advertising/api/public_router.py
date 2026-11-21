from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends

from app.api.dependencies.public import get_current_user
from app.api.dependencies.rate_limit import user_rate_limit
from app.api.errors import error_responses
from app.core.constants import TIME_1M
from app.core.enums import AdLinkChannel
from app.modules.advertising.api.schemas import (
    ActivateAdLinkPromocodeRequest,
    AdLinkPromocodeResultResponse,
)
from app.modules.advertising.dto import ActivateAdLinkPromocodeDto
from app.modules.advertising.usecases import ActivateAdLinkPromocodeUseCase
from app.modules.users import UserDto

router = APIRouter(prefix="/advertising", tags=["advertising"])


# the cabinet opened by an ad link (?ad=<code>): its promocode, if it has one
@router.post(
    "/links/actions/activate-promocode",
    response_model=AdLinkPromocodeResultResponse,
    dependencies=[
        Depends(user_rate_limit(key="promocode_activate", limit=10, window_seconds=TIME_1M))
    ],
    responses=error_responses(400, 404, 503),
)
@inject
async def activate_ad_link_promocode(
    payload: ActivateAdLinkPromocodeRequest,
    use_case: FromDishka[ActivateAdLinkPromocodeUseCase],
    actor: UserDto = Depends(get_current_user),
) -> AdLinkPromocodeResultResponse:
    result = await use_case(
        actor,
        ActivateAdLinkPromocodeDto(ad_link_code=payload.code, channel=AdLinkChannel.WEBCABINET),
    )
    if result is None:
        return AdLinkPromocodeResultResponse()
    return AdLinkPromocodeResultResponse.model_validate(result, from_attributes=True)
