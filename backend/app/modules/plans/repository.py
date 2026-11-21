from decimal import Decimal
from typing import Final

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.errors import constraint_name
from app.core.db.filters import in_ids
from app.core.db.models.active_locale import ActiveLocale
from app.core.db.models.plan import Plan
from app.core.db.models.plan_duration import PlanDuration
from app.core.db.models.plan_price_override import PlanPriceOverride
from app.core.db.models.plan_translation import PlanTranslation
from app.core.db.partial_update import apply_partial_update
from app.core.enums import Currency, PlanAvailability
from app.core.error_codes import ErrorCode
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.plans.dto import (
    CreatePlanDto,
    PlanDto,
    PlanDurationDto,
    PlanPriceOverrideDto,
    PlanTranslationDto,
    ReorderPlansDto,
    SetPlanDurationBasePriceDto,
    SetPlanDurationsDto,
    SetPlanPriceOverrideDto,
    UpdatePlanAccessDto,
    UpdatePlanDiscountDto,
    UpdatePlanDto,
    UpdatePlanSquadsDto,
)

# Fixed pg_advisory_xact_lock keys that serialize writes a read-then-write (or
# delete-then-insert) would otherwise interleave.
_PLAN_ORDER_LOCK_KEY: Final[int] = 8221938
_PLAN_DURATIONS_LOCK_KEY: Final[int] = 8221939


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _load_durations(self, plan_ids: list[int]) -> dict[int, list[PlanDurationDto]]:
        if not plan_ids:
            return {}

        duration_result = await self.session.execute(
            select(PlanDuration)
            .where(in_ids(PlanDuration.plan_id, plan_ids))
            .order_by(PlanDuration.order_index, PlanDuration.id)
        )
        duration_rows = duration_result.scalars().all()
        duration_ids = [row.id for row in duration_rows]

        overrides_by_duration: dict[int, list[PlanPriceOverrideDto]] = {}
        if duration_ids:
            override_result = await self.session.execute(
                select(PlanPriceOverride)
                .where(in_ids(PlanPriceOverride.plan_duration_id, duration_ids))
                .order_by(PlanPriceOverride.id)
            )
            for override_row in override_result.scalars():
                overrides_by_duration.setdefault(override_row.plan_duration_id, []).append(
                    PlanPriceOverrideDto(currency=override_row.currency, price=override_row.price)
                )

        durations_by_plan: dict[int, list[PlanDurationDto]] = {}
        for row in duration_rows:
            durations_by_plan.setdefault(row.plan_id, []).append(
                PlanDurationDto(
                    id=row.id,
                    days=row.days,
                    order_index=row.order_index,
                    base_price=row.base_price,
                    overrides=overrides_by_duration.get(row.id, []),
                )
            )
        return durations_by_plan

    async def _load_translations(self, plan_ids: list[int]) -> dict[int, list[PlanTranslationDto]]:
        # a switched-off locale's translations stay stored for when it comes back
        result = await self.session.execute(
            select(PlanTranslation)
            .where(
                in_ids(PlanTranslation.plan_id, plan_ids),
                PlanTranslation.locale.in_(select(ActiveLocale.locale)),
            )
            .order_by(PlanTranslation.locale)
        )
        translations_by_plan: dict[int, list[PlanTranslationDto]] = {}
        for row in result.scalars():
            translations_by_plan.setdefault(row.plan_id, []).append(self._to_translation_dto(row))
        return translations_by_plan

    async def _replace_translations(
        self, plan_id: int, translations: list[PlanTranslationDto]
    ) -> None:
        await self.session.execute(
            delete(PlanTranslation).where(
                PlanTranslation.plan_id == plan_id,
                or_(
                    PlanTranslation.locale.in_(select(ActiveLocale.locale)),
                    PlanTranslation.locale.in_([item.locale for item in translations]),
                ),
            )
        )
        self.session.add_all(
            PlanTranslation(
                plan_id=plan_id,
                locale=item.locale,
                name=item.name,
                description=item.description,
            )
            for item in translations
        )
        await self.session.flush()

    async def _to_dtos(self, rows: list[Plan]) -> list[PlanDto]:
        plan_ids = [row.id for row in rows]
        durations_by_plan = await self._load_durations(plan_ids)
        translations_by_plan = await self._load_translations(plan_ids)
        return [
            self._to_dto(
                row, durations_by_plan.get(row.id, []), translations_by_plan.get(row.id, [])
            )
            for row in rows
        ]

    async def _hydrate(self, row: Plan) -> PlanDto:
        return (await self._to_dtos([row]))[0]

    def _to_translation_dto(self, row: PlanTranslation) -> PlanTranslationDto:
        return PlanTranslationDto(locale=row.locale, name=row.name, description=row.description)

    def _to_dto(
        self,
        row: Plan,
        durations: list[PlanDurationDto],
        translations: list[PlanTranslationDto],
    ) -> PlanDto:
        return PlanDto(
            id=row.id,
            public_code=row.public_code,
            name=row.name,
            description=row.description,
            tag=row.tag,
            badge=row.badge,
            icon_emoji_id=row.icon_emoji_id,
            icon_emoji_fallback=row.icon_emoji_fallback,
            type=row.type,
            availability=row.availability,
            traffic_limit_strategy=row.traffic_limit_strategy,
            traffic_limit=row.traffic_limit,
            device_limit=row.device_limit,
            allowed_telegram_ids=list(row.allowed_telegram_ids),
            allowed_emails=list(row.allowed_emails),
            internal_squads=list(row.internal_squads),
            external_squad=row.external_squad,
            order_index=row.order_index,
            is_active=row.is_active,
            is_trial=row.is_trial,
            allow_bonus_days=row.allow_bonus_days,
            allow_promocodes=row.allow_promocodes,
            allow_renewal=row.allow_renewal,
            discount_percent=row.discount_percent,
            discount_starts_at=row.discount_starts_at,
            discount_ends_at=row.discount_ends_at,
            durations=durations,
            translations=translations,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def _list(self, *, active_only: bool) -> list[PlanDto]:
        query = select(Plan).order_by(Plan.order_index, Plan.id)
        if active_only:
            query = query.where(Plan.is_active.is_(True))

        result = await self.session.execute(query)
        return await self._to_dtos(list(result.scalars().all()))

    async def list_all(self) -> list[PlanDto]:
        return await self._list(active_only=False)

    async def list_active(self) -> list[PlanDto]:
        return await self._list(active_only=True)

    async def list_by_ids(self, plan_ids: list[int]) -> list[PlanDto]:
        result = await self.session.execute(select(Plan).where(in_ids(Plan.id, plan_ids)))
        return await self._to_dtos(list(result.scalars().all()))

    async def _get_or_raise(self, plan_id: int) -> Plan:
        row = await self.session.get(Plan, plan_id)
        if row is None:
            raise NotFoundError(f"Plan '{plan_id}' not found", code=ErrorCode.PLAN_NOT_FOUND)
        return row

    async def get_by_id(self, plan_id: int) -> PlanDto:
        row = await self._get_or_raise(plan_id)
        return await self._hydrate(row)

    async def get_by_id_for_update(self, plan_id: int) -> PlanDto:
        row = await self.session.get(Plan, plan_id, with_for_update=True, populate_existing=True)
        if row is None:
            raise NotFoundError(f"Plan '{plan_id}' not found", code=ErrorCode.PLAN_NOT_FOUND)
        return await self._hydrate(row)

    async def create(self, data: CreatePlanDto) -> PlanDto:
        await self.session.execute(select(func.pg_advisory_xact_lock(_PLAN_ORDER_LOCK_KEY)))
        count_result = await self.session.execute(select(func.count()).select_from(Plan))
        next_order_index = count_result.scalar_one()

        assert data.public_code is not None  # filled in by CreatePlanUseCase
        row = Plan(
            public_code=data.public_code,
            name=data.name,
            description=data.description,
            tag=data.tag,
            badge=data.badge,
            icon_emoji_id=data.icon_emoji_id,
            icon_emoji_fallback=data.icon_emoji_fallback,
            type=data.type,
            availability=PlanAvailability.ALL,
            traffic_limit_strategy=data.traffic_limit_strategy,
            traffic_limit=data.traffic_limit,
            device_limit=data.device_limit,
            allowed_telegram_ids=[],
            allowed_emails=[],
            internal_squads=[],
            external_squad=None,
            order_index=next_order_index,
            is_active=False,
            is_trial=data.is_trial,
            allow_bonus_days=data.allow_bonus_days,
            allow_promocodes=data.allow_promocodes,
            allow_renewal=data.allow_renewal,
        )
        self.session.add(row)

        try:
            await self.session.flush()
        except IntegrityError as error:
            constraint = constraint_name(error)
            if constraint == "ix_plans_public_code":
                raise ConflictError(
                    f"Plan with public_code '{data.public_code}' already exists",
                    code=ErrorCode.PLAN_PUBLIC_CODE_TAKEN,
                ) from error
            if constraint == "ix_plans_name":
                raise ConflictError(
                    f"Plan with name '{data.name}' already exists", code=ErrorCode.PLAN_NAME_TAKEN
                ) from error
            raise

        await self._replace_translations(row.id, data.translations)
        await self.session.refresh(row)
        return await self._hydrate(row)

    async def update(self, data: UpdatePlanDto) -> PlanDto:
        row = await self._get_or_raise(data.plan_id)
        apply_partial_update(row, data, skip=frozenset({"plan_id", "cleared", "translations"}))
        for name in data.cleared:
            setattr(row, name, None)

        try:
            await self.session.flush()
        except IntegrityError as error:
            constraint = constraint_name(error)
            if constraint == "ix_plans_name":
                raise ConflictError(
                    f"Plan name '{data.name}' already exists", code=ErrorCode.PLAN_NAME_TAKEN
                ) from error
            raise

        if data.translations is not None:
            await self._replace_translations(row.id, data.translations)
        await self.session.refresh(row)
        return await self._hydrate(row)

    async def delete(self, plan_id: int) -> None:
        await self._get_or_raise(plan_id)
        try:
            await self.session.execute(delete(Plan).where(Plan.id == plan_id))
            await self.session.flush()
        except IntegrityError as error:
            constraint = constraint_name(error)
            if constraint == "fk_promocodes_reward_plan_id_plans":
                raise ConflictError(
                    f"Plan '{plan_id}' is still referenced by a promocode",
                    code=ErrorCode.PLAN_USED_BY_PROMOCODE,
                ) from error
            # not transactions: their plan_id is ON DELETE SET NULL, since an
            # order is provisioned from its own plan_snapshot
            raise ConflictError(
                f"Plan '{plan_id}' is still referenced elsewhere", code=ErrorCode.PLAN_IN_USE
            ) from error

    async def set_durations(self, data: SetPlanDurationsDto) -> PlanDto:
        row = await self._get_or_raise(data.plan_id)
        kept_days = {duration.days for duration in data.durations}

        await self.session.execute(select(func.pg_advisory_xact_lock(_PLAN_DURATIONS_LOCK_KEY)))
        # updated in place by days: a kept duration keeps its pinned per-currency prices
        existing = {
            row.days: row
            for row in await self.session.scalars(
                select(PlanDuration).where(PlanDuration.plan_id == data.plan_id)
            )
        }
        await self.session.execute(
            delete(PlanDuration).where(
                PlanDuration.plan_id == data.plan_id, PlanDuration.days.not_in(kept_days)
            )
        )
        for duration in data.durations:
            kept = existing.get(duration.days)
            if kept is None:
                self.session.add(
                    PlanDuration(
                        plan_id=data.plan_id,
                        days=duration.days,
                        order_index=duration.order_index,
                        base_price=duration.base_price,
                    )
                )
            else:
                kept.order_index = duration.order_index
                kept.base_price = duration.base_price
        await self.session.flush()

        return await self._hydrate(row)

    async def set_price_override(self, data: SetPlanPriceOverrideDto) -> PlanDto:
        row = await self._get_or_raise(data.plan_id)

        duration_result = await self.session.execute(
            select(PlanDuration).where(
                PlanDuration.plan_id == data.plan_id, PlanDuration.days == data.days
            )
        )
        duration_row = duration_result.scalar_one_or_none()
        if duration_row is None:
            raise NotFoundError(
                f"Plan '{data.plan_id}' has no duration of '{data.days}' days",
                code=ErrorCode.PLAN_DURATION_NOT_FOUND,
            )

        if data.price is None:
            await self.session.execute(
                delete(PlanPriceOverride).where(
                    PlanPriceOverride.plan_duration_id == duration_row.id,
                    PlanPriceOverride.currency == data.currency,
                )
            )
        else:
            override_result = await self.session.execute(
                select(PlanPriceOverride).where(
                    PlanPriceOverride.plan_duration_id == duration_row.id,
                    PlanPriceOverride.currency == data.currency,
                )
            )
            override_row = override_result.scalar_one_or_none()
            if override_row is None:
                self.session.add(
                    PlanPriceOverride(
                        plan_duration_id=duration_row.id, currency=data.currency, price=data.price
                    )
                )
            else:
                override_row.price = data.price

        await self.session.flush()
        return await self._hydrate(row)

    async def set_duration_base_price(self, data: SetPlanDurationBasePriceDto) -> PlanDto:
        row = await self._get_or_raise(data.plan_id)

        duration_result = await self.session.execute(
            select(PlanDuration).where(
                PlanDuration.plan_id == data.plan_id, PlanDuration.days == data.days
            )
        )
        duration_row = duration_result.scalar_one_or_none()
        if duration_row is None:
            raise NotFoundError(
                f"Plan '{data.plan_id}' has no duration of '{data.days}' days",
                code=ErrorCode.PLAN_DURATION_NOT_FOUND,
            )

        duration_row.base_price = data.base_price
        await self.session.flush()

        return await self._hydrate(row)

    async def set_duration_base_prices(self, values: dict[int, Decimal]) -> None:
        for duration_id, base_price in values.items():
            row = await self.session.get(PlanDuration, duration_id)
            if row is None:
                raise NotFoundError(
                    f"Plan duration '{duration_id}' not found",
                    code=ErrorCode.PLAN_DURATION_NOT_FOUND,
                )
            row.base_price = base_price
        await self.session.flush()

    async def replace_duration_overrides(self, values: dict[int, dict[Currency, Decimal]]) -> None:
        for duration_id, prices in values.items():
            await self.session.execute(
                delete(PlanPriceOverride).where(PlanPriceOverride.plan_duration_id == duration_id)
            )
            self.session.add_all(
                PlanPriceOverride(plan_duration_id=duration_id, currency=currency, price=price)
                for currency, price in prices.items()
            )
        await self.session.flush()

    async def update_access(self, data: UpdatePlanAccessDto) -> PlanDto:
        row = await self._get_or_raise(data.plan_id)
        row.availability = data.availability
        row.allowed_telegram_ids = data.allowed_telegram_ids
        row.allowed_emails = data.allowed_emails

        await self.session.flush()
        await self.session.refresh(row)
        return await self._hydrate(row)

    async def update_squads(self, data: UpdatePlanSquadsDto) -> PlanDto:
        row = await self._get_or_raise(data.plan_id)
        row.internal_squads = data.internal_squads
        row.external_squad = data.external_squad

        await self.session.flush()
        await self.session.refresh(row)
        return await self._hydrate(row)

    async def update_discount(self, data: UpdatePlanDiscountDto) -> PlanDto:
        row = await self._get_or_raise(data.plan_id)
        row.discount_percent = data.discount_percent
        row.discount_starts_at = data.discount_starts_at
        row.discount_ends_at = data.discount_ends_at

        await self.session.flush()
        await self.session.refresh(row)
        return await self._hydrate(row)

    async def reorder(self, data: ReorderPlansDto) -> list[PlanDto]:
        await self.session.execute(select(func.pg_advisory_xact_lock(_PLAN_ORDER_LOCK_KEY)))
        result = await self.session.execute(select(Plan))
        rows = {row.id: row for row in result.scalars()}

        if set(data.plan_ids) != set(rows) or len(data.plan_ids) != len(rows):
            raise ValidationError(
                "Reorder must include every existing plan id exactly once",
                code=ErrorCode.PLAN_REORDER_INCOMPLETE,
            )

        for index, plan_id in enumerate(data.plan_ids):
            rows[plan_id].order_index = index

        await self.session.flush()
        return await self.list_all()
