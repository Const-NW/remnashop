import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.db.models.plan import Plan
from app.core.db.uow import UnitOfWork
from app.core.enums import Locale, PlanAvailability, PlanBadge, PlanType, Role
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.core.ports import EventPublisher
from app.modules.plans.dto import (
    CreatePlanDto,
    PlanDto,
    SetPlanDurationDto,
    SetPlanDurationsDto,
    UpdatePlanAccessDto,
    UpdatePlanDto,
    UpdatePlanSquadsDto,
)
from app.modules.plans.repository import PlanRepository
from app.modules.plans.usecases import (
    CreatePlanUseCase,
    SetPlanDurationsUseCase,
    UpdatePlanAccessUseCase,
    UpdatePlanUseCase,
)
from app.modules.users.dto import UserDto
from tests.fakes.events import RecordingEventPublisher

OWNER: Final[UserDto] = UserDto(login="owner", name="owner", role=Role.OWNER, language=Locale.EN)


@dataclass(frozen=True)
class _Plans:
    repository: PlanRepository
    uow: UnitOfWork
    events: EventPublisher

    async def create(self, data: CreatePlanDto) -> PlanDto:
        return await CreatePlanUseCase(self.repository, self.uow, self.events)(OWNER, data)

    async def update(self, data: UpdatePlanDto) -> PlanDto:
        return await UpdatePlanUseCase(self.repository, self.uow, self.events)(OWNER, data)

    async def set_durations(self, data: SetPlanDurationsDto) -> PlanDto:
        return await SetPlanDurationsUseCase(self.repository, self.uow, self.events)(OWNER, data)

    async def update_access(self, data: UpdatePlanAccessDto) -> PlanDto:
        return await UpdatePlanAccessUseCase(self.repository, self.uow, self.events)(OWNER, data)


@pytest.fixture
def plans(
    plan_repository: PlanRepository, uow: UnitOfWork, event_publisher: EventPublisher
) -> _Plans:
    return _Plans(plan_repository, uow, event_publisher)


def _create_dto(public_code: str = "basic", name: str = "Basic") -> CreatePlanDto:
    return CreatePlanDto(
        public_code=public_code, name=name, type=PlanType.BOTH, traffic_limit=100, device_limit=3
    )


async def _add_minimal_squad(plan_repository: PlanRepository, plan_id: int) -> None:
    await plan_repository.update_squads(
        UpdatePlanSquadsDto(plan_id=plan_id, internal_squads=[uuid4()], external_squad=None)
    )


def _one_duration(plan_id: int) -> SetPlanDurationsDto:
    return SetPlanDurationsDto(
        plan_id=plan_id,
        durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("9.99"))],
    )


async def _add_minimal_duration(plans: _Plans, plan_id: int) -> None:
    await plans.set_durations(_one_duration(plan_id))


async def test_activating_plan_without_durations_raises_validation_error(
    plans: _Plans,
) -> None:
    created = await plans.create(_create_dto())
    await _add_minimal_squad(plans.repository, created.id)

    with pytest.raises(ValidationError):
        await plans.update(UpdatePlanDto(plan_id=created.id, is_active=True))


async def test_activating_plan_without_squads_raises_validation_error(
    plans: _Plans,
) -> None:
    created = await plans.create(_create_dto())
    await _add_minimal_duration(plans, created.id)

    with pytest.raises(ValidationError):
        await plans.update(UpdatePlanDto(plan_id=created.id, is_active=True))


async def test_clearing_durations_of_active_plan_raises_validation_error(
    plans: _Plans,
) -> None:
    created = await plans.create(_create_dto())
    await _add_minimal_squad(plans.repository, created.id)
    await _add_minimal_duration(plans, created.id)
    await plans.update(UpdatePlanDto(plan_id=created.id, is_active=True))

    with pytest.raises(ValidationError):
        await plans.set_durations(SetPlanDurationsDto(plan_id=created.id, durations=[]))


async def test_create_devices_plan_zeroes_traffic_limit(plans: _Plans) -> None:
    created = await plans.create(
        CreatePlanDto(
            public_code="devices-only",
            name="Devices Only",
            type=PlanType.DEVICES,
            traffic_limit=500,
            device_limit=5,
        )
    )

    assert created.traffic_limit == 0
    assert created.device_limit == 5


async def test_create_traffic_plan_zeroes_device_limit(plans: _Plans) -> None:
    created = await plans.create(
        CreatePlanDto(
            public_code="traffic-only",
            name="Traffic Only",
            type=PlanType.TRAFFIC,
            traffic_limit=500,
            device_limit=5,
        )
    )

    assert created.traffic_limit == 500
    assert created.device_limit == 0


async def test_create_unlimited_plan_zeroes_both_limits(plans: _Plans) -> None:
    created = await plans.create(
        CreatePlanDto(
            public_code="fully-unlimited",
            name="Fully Unlimited",
            type=PlanType.UNLIMITED,
            traffic_limit=500,
            device_limit=5,
        )
    )

    assert created.traffic_limit == 0
    assert created.device_limit == 0


async def test_update_switching_to_devices_type_zeroes_traffic_limit(
    plans: _Plans,
) -> None:
    created = await plans.create(_create_dto())

    updated = await plans.update(UpdatePlanDto(plan_id=created.id, type=PlanType.DEVICES))

    assert updated.traffic_limit == 0
    assert updated.device_limit == 3  # untouched


@pytest.mark.parametrize(
    ("plan_type", "traffic_limit", "device_limit"),
    [(PlanType.TRAFFIC, 0, 0), (PlanType.DEVICES, 0, 0), (PlanType.BOTH, 100, 0)],
)
async def test_create_rejects_a_zero_limit_the_type_applies(
    plans: _Plans, plan_type: PlanType, traffic_limit: int, device_limit: int
) -> None:
    with pytest.raises(ValidationError) as error:
        await plans.create(
            CreatePlanDto(
                public_code="zero",
                name="Zero",
                type=plan_type,
                traffic_limit=traffic_limit,
                device_limit=device_limit,
            )
        )

    assert error.value.code == ErrorCode.PLAN_LIMIT_REQUIRED


async def test_update_to_a_limited_type_needs_its_limit(plans: _Plans) -> None:
    created = await plans.create(
        CreatePlanDto(
            public_code="unlimited",
            name="Unlimited",
            type=PlanType.UNLIMITED,
            traffic_limit=0,
            device_limit=0,
        )
    )

    with pytest.raises(ValidationError):
        await plans.update(UpdatePlanDto(plan_id=created.id, type=PlanType.TRAFFIC))

    updated = await plans.update(
        UpdatePlanDto(plan_id=created.id, type=PlanType.TRAFFIC, traffic_limit=50)
    )
    assert (updated.traffic_limit, updated.device_limit) == (50, 0)


async def test_clearing_the_icon_drops_its_fallback(plans: _Plans) -> None:
    created = await plans.create(
        CreatePlanDto(
            public_code="icon",
            name="Icon",
            type=PlanType.UNLIMITED,
            traffic_limit=0,
            device_limit=0,
            icon_emoji_id="123",
            icon_emoji_fallback="🚀",
        )
    )

    cleared = await plans.update(
        UpdatePlanDto(plan_id=created.id, cleared=frozenset({"icon_emoji_id"}))
    )

    assert (cleared.icon_emoji_id, cleared.icon_emoji_fallback) == (None, None)


async def test_set_durations_rejects_duplicate_days(plans: _Plans) -> None:
    created = await plans.create(_create_dto())

    with pytest.raises(ValidationError):
        await plans.set_durations(
            SetPlanDurationsDto(
                plan_id=created.id,
                durations=[
                    SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("3.99")),
                    SetPlanDurationDto(days=30, order_index=1, base_price=Decimal("9.99")),
                ],
            )
        )


async def test_update_access_rejects_allowed_with_empty_allowlists(
    plans: _Plans,
) -> None:
    created = await plans.create(_create_dto())

    with pytest.raises(ValidationError):
        await plans.update_access(
            UpdatePlanAccessDto(
                plan_id=created.id,
                availability=PlanAvailability.ALLOWED,
                allowed_telegram_ids=[],
                allowed_emails=[],
            )
        )


async def test_update_access_rejects_existing_for_a_trial_plan(
    plans: _Plans,
) -> None:
    created = await plans.create(replace(_create_dto(), is_trial=True))

    with pytest.raises(ValidationError) as error:
        await plans.update_access(
            UpdatePlanAccessDto(
                plan_id=created.id,
                availability=PlanAvailability.EXISTING,
                allowed_telegram_ids=[],
                allowed_emails=[],
            )
        )
    assert error.value.code == ErrorCode.PLAN_TRIAL_EXISTING_ACCESS


async def test_update_rejects_turning_an_existing_only_plan_into_a_trial(
    plans: _Plans,
) -> None:
    created = await plans.create(_create_dto())
    await plans.update_access(
        UpdatePlanAccessDto(
            plan_id=created.id,
            availability=PlanAvailability.EXISTING,
            allowed_telegram_ids=[],
            allowed_emails=[],
        )
    )

    with pytest.raises(ValidationError) as error:
        await plans.update(UpdatePlanDto(plan_id=created.id, is_trial=True))
    assert error.value.code == ErrorCode.PLAN_TRIAL_EXISTING_ACCESS


async def test_create_rejects_a_trial_plan_with_a_badge(plans: _Plans) -> None:
    with pytest.raises(ValidationError) as error:
        await plans.create(replace(_create_dto(), is_trial=True, badge=PlanBadge.POPULAR))
    assert error.value.code == ErrorCode.PLAN_TRIAL_BADGE


async def test_update_rejects_a_badge_on_a_trial_plan(plans: _Plans) -> None:
    created = await plans.create(replace(_create_dto(), badge=PlanBadge.POPULAR))

    with pytest.raises(ValidationError) as error:
        await plans.update(UpdatePlanDto(plan_id=created.id, is_trial=True))
    assert error.value.code == ErrorCode.PLAN_TRIAL_BADGE


async def test_update_turns_a_plan_into_a_trial_when_the_badge_is_cleared(
    plans: _Plans,
) -> None:
    created = await plans.create(replace(_create_dto(), badge=PlanBadge.POPULAR))

    updated = await plans.update(
        UpdatePlanDto(plan_id=created.id, is_trial=True, cleared=frozenset({"badge"}))
    )

    assert updated.is_trial is True
    assert updated.badge is None


_RACE_PLAN_NAME: Final[str] = "plan-race"
# long enough for an activation that isn't blocked to reach its commit
_BLOCKED_FOR_S: Final[float] = 0.5


@pytest.fixture
async def race_plan(engine: AsyncEngine) -> AsyncIterator[int]:
    # each side of the race needs its own connection, so the plan is committed
    # and removed by hand
    try:
        async with engine.connect() as connection:
            session = AsyncSession(bind=connection, expire_on_commit=False)
            repository = PlanRepository(session)
            plans = _Plans(repository, UnitOfWork(session), RecordingEventPublisher())
            plan = await plans.create(_create_dto("race", _RACE_PLAN_NAME))
            async with plans.uow:
                await _add_minimal_squad(repository, plan.id)
            await plans.set_durations(_one_duration(plan.id))
            await session.close()
        yield plan.id
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(delete(Plan).where(Plan.name == _RACE_PLAN_NAME))


async def test_activation_waits_for_durations_being_removed(
    engine: AsyncEngine, race_plan: int
) -> None:
    async with engine.connect() as removing_conn, engine.connect() as activating_conn:
        removing = PlanRepository(AsyncSession(bind=removing_conn, expire_on_commit=False))
        removing_uow = UnitOfWork(removing.session)
        activating_session = AsyncSession(bind=activating_conn, expire_on_commit=False)
        activating = _Plans(
            PlanRepository(activating_session),
            UnitOfWork(activating_session),
            RecordingEventPublisher(),
        )

        async with removing_uow:
            await removing.get_by_id_for_update(race_plan)
            await removing.set_durations(SetPlanDurationsDto(plan_id=race_plan, durations=[]))
            activation = asyncio.create_task(
                activating.update(UpdatePlanDto(plan_id=race_plan, is_active=True))
            )
            await asyncio.sleep(_BLOCKED_FOR_S)
            assert not activation.done()

        with pytest.raises(ValidationError) as error:
            await activation
        assert error.value.code is ErrorCode.PLAN_NO_DURATIONS
        await removing.session.close()
        await activating_session.close()
