import math
from datetime import datetime, timedelta
from typing import Final

from loguru import logger
from redis.asyncio import Redis

from app.core.actor import Actor
from app.core.constants import REDIS_KEY_PREFIX, TIME_1H, TIME_1M, TTL_7D
from app.core.db.uow import UnitOfWork
from app.core.ports import EventPublisher
from app.core.usecase import UseCase
from app.core.utils.time import datetime_now
from app.modules.promocodes.dto import PromocodeDto
from app.modules.promocodes.events import PromocodeDiscountExpiringEvent
from app.modules.promocodes.repository import PromocodeRepository
from app.modules.users import UserDto, UserRepository

# a quarter of the code's life, within these: a sale of a few hours warns 30-60
# minutes ahead, one of four days or more a day ahead
_WARN_BEFORE_MIN: Final[timedelta] = timedelta(minutes=30)
_WARN_BEFORE_MAX: Final[timedelta] = timedelta(hours=24)
# outlives the warning window; the key carries expires_at, so an extension warns again
_WARNED_TTL_S: Final[int] = TTL_7D


def _warned_key(promocode: PromocodeDto, user_id: int) -> str:
    assert promocode.expires_at is not None
    expires = int(promocode.expires_at.timestamp())
    return f"{REDIS_KEY_PREFIX}promocode_discount_warned:{promocode.id}:{expires}:{user_id}"


def _warn_before(promocode: PromocodeDto) -> timedelta:
    assert promocode.expires_at is not None
    life = promocode.expires_at - (promocode.created_at or promocode.expires_at)
    return min(_WARN_BEFORE_MAX, max(_WARN_BEFORE_MIN, life / 4))


def _percents(user: UserDto, promocode_ids: set[int]) -> dict[int, int]:
    # both discounts from one promocode make one warning, with the larger percent
    percents: dict[int, int] = {}
    for promocode_id, percent in (
        (user.personal_discount_promocode_id, user.personal_discount),
        (user.purchase_discount_promocode_id, user.purchase_discount),
    ):
        if promocode_id in promocode_ids and percent > 0:
            percents[promocode_id] = max(percent, percents.get(promocode_id, 0))
    return percents


class WarnExpiringPromocodeDiscountsUseCase(UseCase[None, int]):
    required_permission = None

    def __init__(
        self,
        promocode_repository: PromocodeRepository,
        user_repository: UserRepository,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
        redis: Redis,
    ) -> None:
        self._promocode_repository = promocode_repository
        self._user_repository = user_repository
        self._uow = uow
        self._event_publisher = event_publisher
        self._redis = redis

    async def _execute(self, actor: Actor, data: None) -> int:
        now = datetime_now()
        # claimed before the publish: a run redelivered after its commit finds them
        # taken; a failed commit gives them back for the next run
        keys: list[str] = []
        try:
            return await self._warn(actor, now, keys)
        except BaseException:
            if keys:
                await self._redis.delete(*keys)
            raise

    async def _warn(self, actor: Actor, now: datetime, keys: list[str]) -> int:
        async with self._uow:
            expiring = await self._promocode_repository.list_expiring(now, now + _WARN_BEFORE_MAX)
            promocodes = {
                promocode.id: promocode
                for promocode in expiring
                if promocode.expires_at is not None
                and promocode.expires_at - now <= _warn_before(promocode)
            }
            if not promocodes:
                return 0
            users = await self._user_repository.list_with_discounts_from(list(promocodes))
            candidates = [
                (user, promocodes[promocode_id], percent)
                for user in users
                for promocode_id, percent in _percents(user, set(promocodes)).items()
            ]
            if not candidates:
                return 0
            for user, promocode, percent in candidates:
                key = _warned_key(promocode, user.id)
                if not await self._redis.set(key, "1", nx=True, ex=_WARNED_TTL_S):
                    continue
                keys.append(key)
                assert promocode.expires_at is not None
                left = (promocode.expires_at - now).total_seconds()
                await self._event_publisher.publish(
                    PromocodeDiscountExpiringEvent(
                        user=user,
                        promocode_id=promocode.id,
                        code=promocode.code,
                        percent=percent,
                        hours=max(1, math.ceil(left / TIME_1H)),
                        minutes=max(1, math.ceil(left / TIME_1M)) if left < TIME_1H else None,
                    )
                )
        logger.info(f"{actor.log} Warned {len(keys)} customers about expiring promocode discounts")
        return len(keys)
