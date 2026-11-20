from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import Currency, Locale, PlanAvailability, PlanType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.plans.api.schemas import CreatePlanRequest, PlanIconRequest
from app.modules.plans.dto import (
    CreatePlanDto,
    PlanDto,
    PlanTranslationDto,
    ReorderPlansDto,
    SetPlanDurationBasePriceDto,
    SetPlanDurationDto,
    SetPlanDurationsDto,
    SetPlanPriceOverrideDto,
    UpdatePlanAccessDto,
    UpdatePlanDto,
    UpdatePlanSquadsDto,
)
from app.modules.plans.repository import PlanRepository
from app.modules.translations import TranslationRepository


def _create_dto(public_code: str = "basic", name: str = "Basic") -> CreatePlanDto:
    return CreatePlanDto(
        public_code=public_code, name=name, type=PlanType.BOTH, traffic_limit=100, device_limit=3
    )


async def _make_plan(plan_repository: PlanRepository) -> PlanDto:
    return await plan_repository.create(_create_dto())


async def _add_minimal_squad(plan_repository: PlanRepository, plan_id: int) -> None:
    await plan_repository.update_squads(
        UpdatePlanSquadsDto(plan_id=plan_id, internal_squads=[uuid4()], external_squad=None)
    )


async def _add_minimal_duration(plan_repository: PlanRepository, plan_id: int) -> None:
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan_id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("9.99"))],
        )
    )


async def test_create_then_list_all_returns_plan(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())

    plans = await plan_repository.list_all()

    assert len(plans) == 1
    assert plans[0].id == created.id
    assert plans[0].name == "Basic"
    assert plans[0].is_active is False
    assert plans[0].durations == []


async def test_new_plan_starts_inactive_and_excluded_from_list_active(
    plan_repository: PlanRepository,
) -> None:
    await plan_repository.create(_create_dto())

    assert await plan_repository.list_active() == []


async def test_activating_plan_makes_it_appear_in_list_active(
    plan_repository: PlanRepository,
) -> None:
    created = await plan_repository.create(_create_dto())
    await _add_minimal_squad(plan_repository, created.id)
    await _add_minimal_duration(plan_repository, created.id)

    await plan_repository.update(UpdatePlanDto(plan_id=created.id, is_active=True))

    active = await plan_repository.list_active()
    assert [plan.id for plan in active] == [created.id]


async def test_create_assigns_incrementing_order_index(plan_repository: PlanRepository) -> None:
    first = await plan_repository.create(_create_dto("a", "A"))
    second = await plan_repository.create(_create_dto("b", "B"))

    assert first.order_index == 0
    assert second.order_index == 1


async def test_create_duplicate_public_code_raises_conflict(
    plan_repository: PlanRepository,
) -> None:
    await plan_repository.create(_create_dto("dup", "First"))

    with pytest.raises(ConflictError):
        await plan_repository.create(_create_dto("dup", "Second"))


async def test_create_duplicate_name_raises_conflict(plan_repository: PlanRepository) -> None:
    await plan_repository.create(_create_dto("first", "Dup"))

    with pytest.raises(ConflictError):
        await plan_repository.create(_create_dto("second", "Dup"))


async def test_create_both_plan_keeps_both_limits(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())

    assert created.traffic_limit == 100
    assert created.device_limit == 3


async def test_update_unknown_plan_raises_not_found(plan_repository: PlanRepository) -> None:
    with pytest.raises(NotFoundError):
        await plan_repository.update(UpdatePlanDto(plan_id=999_999, name="X"))


async def test_partial_update_leaves_other_fields_untouched(
    plan_repository: PlanRepository,
) -> None:
    created = await plan_repository.create(_create_dto())

    updated = await plan_repository.update(UpdatePlanDto(plan_id=created.id, traffic_limit=200))

    assert updated.traffic_limit == 200
    assert updated.device_limit == 3  # untouched
    assert updated.name == "Basic"  # untouched


async def test_update_on_an_already_devices_plan_keeps_traffic_limit_zeroed(
    plan_repository: PlanRepository,
) -> None:
    created = await plan_repository.create(
        CreatePlanDto(
            public_code="devices-plan",
            name="Devices Plan",
            type=PlanType.DEVICES,
            traffic_limit=0,
            device_limit=3,
        )
    )

    # an unrelated update: type stays DEVICES, traffic_limit stays zeroed
    updated = await plan_repository.update(UpdatePlanDto(plan_id=created.id, name="Renamed"))

    assert updated.traffic_limit == 0
    assert updated.name == "Renamed"


async def test_delete_removes_plan(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())

    await plan_repository.delete(created.id)

    assert await plan_repository.list_all() == []


async def test_delete_unknown_plan_raises_not_found(plan_repository: PlanRepository) -> None:
    with pytest.raises(NotFoundError):
        await plan_repository.delete(999_999)


async def test_set_durations_replaces_full_set(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())

    updated = await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=created.id,
            durations=[
                SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("3.99")),
                SetPlanDurationDto(days=90, order_index=1, base_price=Decimal("9.99")),
            ],
        )
    )

    assert [duration.days for duration in updated.durations] == [30, 90]
    assert updated.durations[0].base_price == Decimal("3.99")
    assert updated.durations[0].overrides == []


async def test_set_durations_again_drops_previous_set(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=created.id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("3.99"))],
        )
    )

    updated = await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=created.id,
            durations=[SetPlanDurationDto(days=7, order_index=0, base_price=Decimal("0.99"))],
        )
    )

    assert [duration.days for duration in updated.durations] == [7]


async def test_update_access_persists_allowlists(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())

    updated = await plan_repository.update_access(
        UpdatePlanAccessDto(
            plan_id=created.id,
            availability=PlanAvailability.ALLOWED,
            allowed_telegram_ids=[111, 222],
            allowed_emails=["a@example.com"],
        )
    )

    assert updated.availability == PlanAvailability.ALLOWED
    assert updated.allowed_telegram_ids == [111, 222]
    assert updated.allowed_emails == ["a@example.com"]


async def test_update_squads_persists_squad_assignment(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())
    internal = uuid4()
    external = uuid4()

    updated = await plan_repository.update_squads(
        UpdatePlanSquadsDto(plan_id=created.id, internal_squads=[internal], external_squad=external)
    )

    assert updated.internal_squads == [internal]
    assert updated.external_squad == external


async def test_reorder_applies_new_order(plan_repository: PlanRepository) -> None:
    first = await plan_repository.create(_create_dto("a", "A"))
    second = await plan_repository.create(_create_dto("b", "B"))

    reordered = await plan_repository.reorder(ReorderPlansDto(plan_ids=[second.id, first.id]))

    assert [plan.id for plan in reordered] == [second.id, first.id]


async def test_reorder_rejects_incomplete_id_set(plan_repository: PlanRepository) -> None:
    first = await plan_repository.create(_create_dto("a", "A"))
    await plan_repository.create(_create_dto("b", "B"))

    with pytest.raises(ValidationError):
        await plan_repository.reorder(ReorderPlansDto(plan_ids=[first.id]))


async def test_reorder_rejects_duplicate_id(plan_repository: PlanRepository) -> None:
    first = await plan_repository.create(_create_dto("a", "A"))
    second = await plan_repository.create(_create_dto("b", "B"))

    with pytest.raises(ValidationError):
        await plan_repository.reorder(ReorderPlansDto(plan_ids=[first.id, first.id, second.id]))


async def test_get_by_id_returns_plan(plan_repository: PlanRepository) -> None:
    created = await plan_repository.create(_create_dto())

    fetched = await plan_repository.get_by_id(created.id)

    assert fetched.id == created.id
    assert fetched.name == "Basic"


async def test_get_by_id_unknown_plan_raises_not_found(plan_repository: PlanRepository) -> None:
    with pytest.raises(NotFoundError):
        await plan_repository.get_by_id(999_999)


async def test_set_durations_stores_base_price(
    plan_repository: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(plan_repository)
    result = await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("3.99"))],
        )
    )
    assert result.durations[0].base_price == Decimal("3.99")
    assert result.durations[0].overrides == []


async def test_set_price_override_upserts_and_unpins(
    plan_repository: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(plan_repository)
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("3.99"))],
        )
    )
    pinned = await plan_repository.set_price_override(
        SetPlanPriceOverrideDto(
            plan_id=plan.id,
            days=30,
            currency=Currency.RUB,
            price=Decimal("299"),
        )
    )
    assert pinned.durations[0].overrides[0].currency is Currency.RUB
    unpinned = await plan_repository.set_price_override(
        SetPlanPriceOverrideDto(plan_id=plan.id, days=30, currency=Currency.RUB, price=None)
    )
    assert unpinned.durations[0].overrides == []


async def test_set_duration_base_price_preserves_other_durations_overrides(
    plan_repository: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(plan_repository)
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[
                SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("3.99")),
                SetPlanDurationDto(days=90, order_index=1, base_price=Decimal("9.99")),
            ],
        )
    )
    await plan_repository.set_price_override(
        SetPlanPriceOverrideDto(
            plan_id=plan.id,
            days=30,
            currency=Currency.RUB,
            price=Decimal("299"),
        )
    )

    updated = await plan_repository.set_duration_base_price(
        SetPlanDurationBasePriceDto(plan_id=plan.id, days=90, base_price=Decimal("19.99"))
    )

    by_days = {duration.days: duration for duration in updated.durations}
    assert by_days[90].base_price == Decimal("19.99")
    assert by_days[30].base_price == Decimal("3.99")
    assert by_days[30].overrides[0].currency is Currency.RUB
    assert by_days[30].overrides[0].price == Decimal("299")


async def test_set_duration_base_price_unknown_duration_raises_not_found(
    plan_repository: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(plan_repository)
    with pytest.raises(NotFoundError):
        await plan_repository.set_duration_base_price(
            SetPlanDurationBasePriceDto(plan_id=plan.id, days=30, base_price=Decimal("5"))
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"icon_emoji_id": "1"},
        {"icon_emoji_fallback": "⭐"},
        {"icon_emoji_id": "not-digits", "icon_emoji_fallback": "⭐"},
    ],
)
def test_request_rejects_an_inconsistent_icon(fields: dict[str, str]) -> None:
    with pytest.raises(PydanticValidationError):
        PlanIconRequest(**fields)


def test_create_request_defaults_the_icon_unless_cleared() -> None:
    base = {"name": "Pro", "type": "BOTH", "traffic_limit": 0, "device_limit": 1}
    omitted = CreatePlanRequest.model_validate(base)
    assert (omitted.icon_emoji_id, omitted.icon_emoji_fallback) == ("5429613693287578957", "📦")
    cleared = CreatePlanRequest.model_validate(
        {**base, "icon_emoji_id": None, "icon_emoji_fallback": None}
    )
    assert (cleared.icon_emoji_id, cleared.icon_emoji_fallback) == (None, None)


async def test_set_durations_keeps_pinned_prices_of_kept_durations(
    plan_repository: PlanRepository, session: AsyncSession
) -> None:
    plan = await _make_plan(plan_repository)
    await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[
                SetPlanDurationDto(days=30, order_index=0, base_price=Decimal("3.99")),
                SetPlanDurationDto(days=60, order_index=1, base_price=Decimal("6.99")),
            ],
        )
    )
    await plan_repository.set_price_override(
        SetPlanPriceOverrideDto(
            plan_id=plan.id, days=30, currency=Currency.RUB, price=Decimal("299")
        )
    )

    # 60 dropped, 90 added, 30 moved down
    result = await plan_repository.set_durations(
        SetPlanDurationsDto(
            plan_id=plan.id,
            durations=[
                SetPlanDurationDto(days=90, order_index=0, base_price=Decimal("9.99")),
                SetPlanDurationDto(days=30, order_index=1, base_price=Decimal("4.99")),
            ],
        )
    )

    by_days = {duration.days: duration for duration in result.durations}
    assert set(by_days) == {30, 90}
    assert by_days[30].order_index == 1
    assert by_days[30].base_price == Decimal("4.99")
    assert [(o.currency, o.price) for o in by_days[30].overrides] == [
        (Currency.RUB, Decimal("299"))
    ]
    assert by_days[90].overrides == []


async def _activate(plan_repository: PlanRepository, *locales: Locale) -> None:
    translations = TranslationRepository(plan_repository.session)
    for locale in locales:
        await translations.add_active_locale(locale)


async def test_translations_are_saved_and_replaced(plan_repository: PlanRepository) -> None:
    await _activate(plan_repository, Locale.RU, Locale.DE)
    created = await plan_repository.create(
        replace(
            _create_dto(),
            translations=[
                PlanTranslationDto(locale=Locale.RU, name="Базовый", description="Для всех"),
            ],
        )
    )

    renamed = await plan_repository.update(
        UpdatePlanDto(
            plan_id=created.id,
            translations=[PlanTranslationDto(locale=Locale.RU, name="Базовый 2")],
        )
    )
    updated = await plan_repository.update(
        UpdatePlanDto(
            plan_id=created.id,
            translations=[PlanTranslationDto(locale=Locale.DE, name="Basis")],
        )
    )
    untouched = await plan_repository.update(UpdatePlanDto(plan_id=created.id, tag="VIP"))

    assert created.display_name(Locale.RU) == "Базовый"
    assert created.display_description(Locale.RU) == "Для всех"
    assert [(t.locale, t.name) for t in renamed.translations] == [(Locale.RU, "Базовый 2")]
    assert [(t.locale, t.name) for t in updated.translations] == [(Locale.DE, "Basis")]
    assert untouched.translations == updated.translations
    # a locale with no translation shows the plan's own name
    assert updated.display_name(Locale.RU) == "Basic"


async def test_a_switched_off_locales_translation_is_hidden_and_kept(
    plan_repository: PlanRepository,
) -> None:
    await _activate(plan_repository, Locale.RU)
    created = await plan_repository.create(
        replace(
            _create_dto(),
            translations=[
                PlanTranslationDto(locale=Locale.RU, name="Базовый"),
                PlanTranslationDto(locale=Locale.DE, name="Basis"),
            ],
        )
    )

    # the dashboard sends only the shop's locales
    await plan_repository.update(
        UpdatePlanDto(
            plan_id=created.id,
            translations=[PlanTranslationDto(locale=Locale.RU, name="Базовый 2")],
        )
    )
    await _activate(plan_repository, Locale.DE)
    back = await plan_repository.get_by_id(created.id)

    assert created.display_name(Locale.DE) == "Basic"
    assert [(t.locale, t.name) for t in back.translations] == [
        (Locale.DE, "Basis"),
        (Locale.RU, "Базовый 2"),
    ]


def test_a_translation_without_a_description_shows_the_plans_own() -> None:
    plan = PlanDto(
        public_code="basic",
        name="Basic",
        description="For everyone",
        type=PlanType.BOTH,
        traffic_limit=1,
        device_limit=1,
        translations=[PlanTranslationDto(locale=Locale.RU, name="Базовый")],
    )

    assert plan.display_name(Locale.RU) == "Базовый"
    assert plan.display_description(Locale.RU) == "For everyone"


def test_create_request_rejects_two_translations_for_one_locale() -> None:
    with pytest.raises(PydanticValidationError):
        CreatePlanRequest.model_validate(
            {
                "name": "Basic",
                "type": PlanType.BOTH,
                "traffic_limit": 1,
                "device_limit": 1,
                "translations": [
                    {"locale": "ru", "name": "Базовый"},
                    {"locale": "ru", "name": "Базовый 2"},
                ],
            }
        )
