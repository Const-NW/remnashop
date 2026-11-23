from datetime import datetime, timedelta
from typing import Optional

from app.core.enums import Locale
from app.core.utils.time import datetime_now
from app.modules.subscriptions.api.schemas import OwnSubscriptionResponse, SubscriptionResponse
from app.modules.subscriptions.dto import SubscriptionDto
from tests.modules.subscriptions.factories import make_plan_snapshot


def _dto(grace_until: Optional[datetime]) -> SubscriptionDto:
    return SubscriptionDto.model_validate(
        {
            "id": 1,
            "user_id": 2,
            "remnawave_user_id": 3,
            "status": "EXPIRED",
            "traffic_limit": 100,
            "device_limit": 3,
            "effective_traffic_limit": 100,
            "effective_device_limit": 3,
            "expire_at": datetime_now(),
            "url": "https://example.com/sub/3",
            "plan_snapshot": make_plan_snapshot(),
            "grace_until": grace_until,
        }
    )


def test_both_responses_carry_grace_until() -> None:
    until = datetime_now() + timedelta(days=3)
    dto = _dto(until)

    own = OwnSubscriptionResponse.from_dto(dto, secret="s", locale=Locale.EN)
    admin = SubscriptionResponse.model_validate(dto, from_attributes=True)

    assert own.grace_until == until
    assert admin.grace_until == until


def test_grace_until_is_empty_outside_grace() -> None:
    own = OwnSubscriptionResponse.from_dto(_dto(None), secret="s", locale=Locale.EN)

    assert own.grace_until is None
