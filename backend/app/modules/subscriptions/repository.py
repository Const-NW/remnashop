from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Final, Literal, Optional, cast

from sqlalchemy import (
    ColumnElement,
    Numeric,
    Table,
    and_,
    bindparam,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DEFAULT_PAGE_SIZE, GRACE_ENTRY_WINDOW, INT32_MAX
from app.core.db.filters import in_ids, visible_rows
from app.core.db.models.addon_entitlement import AddOnEntitlement
from app.core.db.models.addon_grant_idempotency import AddOnGrantIdempotency
from app.core.db.models.plan import Plan
from app.core.db.models.plan_duration import PlanDuration
from app.core.db.models.subscription import Subscription
from app.core.db.partial_update import apply_partial_update
from app.core.enums import AddOnType, SubscriptionStatus
from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError
from app.core.utils.time import datetime_now
from app.modules.subscriptions.domain import OWNED_STATUSES, SubscriptionState
from app.modules.subscriptions.dto import (
    AddOnEntitlementDto,
    CreateSubscriptionDto,
    PlanSnapshot,
    SubscriptionConfigUpdateDto,
    SubscriptionDto,
    UpdateSubscriptionDto,
    UpdateSubscriptionSquadsDto,
)
from app.modules.subscriptions.grace import LIVE_STATUSES

# pg_advisory_xact_lock namespace key, paired with a user_id (keys in
# modules/plans/repository.py are 8221938/8221939).
_SUBSCRIPTION_PURCHASE_CAP_LOCK_KEY: Final[int] = 8221940
RECONCILE_EXPIRED_WINDOW: Final[timedelta] = timedelta(days=30)
# the subscriptions a customer is using now, listed first on their own card
_LIVE_STATUSES: Final[tuple[SubscriptionStatus, ...]] = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.LIMITED,
)

ResetColumn = Literal["device_single_reset_at", "device_all_reset_at", "link_reset_at"]


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _load_entitlements(
        self, subscription_ids: list[int]
    ) -> dict[int, list[AddOnEntitlementDto]]:
        if not subscription_ids:
            return {}

        result = await self.session.execute(
            select(AddOnEntitlement)
            .where(in_ids(AddOnEntitlement.subscription_id, subscription_ids))
            .order_by(AddOnEntitlement.id)
        )
        entitlements_by_subscription: dict[int, list[AddOnEntitlementDto]] = {}
        for row in result.scalars():
            entitlements_by_subscription.setdefault(row.subscription_id, []).append(
                AddOnEntitlementDto(
                    subscription_id=row.subscription_id,
                    addon_id=row.addon_id,
                    type=row.type,
                    amount=row.amount,
                    granted_at=row.granted_at,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        return entitlements_by_subscription

    def _to_dto(
        self, row: Subscription, entitlements: list[AddOnEntitlementDto]
    ) -> SubscriptionDto:
        traffic_bonus = sum(e.amount for e in entitlements if e.type == AddOnType.TRAFFIC)
        device_bonus = sum(e.amount for e in entitlements if e.type == AddOnType.DEVICES)
        return SubscriptionDto(
            id=row.id,
            user_id=row.user_id,
            remnawave_user_id=row.remnawave_user_id,
            status=row.status,
            is_trial=row.is_trial,
            traffic_limit=row.traffic_limit,
            device_limit=row.device_limit,
            # 0 = unlimited; an add-on on top must not turn it into a finite cap
            effective_traffic_limit=row.traffic_limit and row.traffic_limit + traffic_bonus,
            effective_device_limit=row.device_limit and row.device_limit + device_bonus,
            traffic_limit_strategy=row.traffic_limit_strategy,
            tag=row.tag,
            internal_squads=list(row.internal_squads),
            external_squad=row.external_squad,
            expire_at=row.expire_at,
            url=row.url,
            plan_snapshot=PlanSnapshot.from_json(dict(row.plan_snapshot)),
            device_single_reset_at=row.device_single_reset_at,
            device_all_reset_at=row.device_all_reset_at,
            link_reset_at=row.link_reset_at,
            first_connected_at=row.first_connected_at,
            panel_created_at=row.panel_created_at,
            term_anchor_day=row.term_anchor_day,
            paused_by_channel_leave=row.paused_by_channel_leave,
            grace_until=row.grace_until,
            grace_started_at=row.grace_started_at,
            grace_ended_at=row.grace_ended_at,
            panel_restore_due_at=row.panel_restore_due_at,
            addon_entitlements=entitlements,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _to_state(self, row: Subscription) -> SubscriptionState:
        return SubscriptionState(
            id=row.id,
            status=row.status,
            expire_at=row.expire_at,
            plan_snapshot=PlanSnapshot.from_json(dict(row.plan_snapshot)),
            traffic_limit=row.traffic_limit,
            device_limit=row.device_limit,
            traffic_limit_strategy=row.traffic_limit_strategy,
            internal_squads=list(row.internal_squads),
            external_squad=row.external_squad,
            is_trial=row.is_trial,
            grace_until=row.grace_until,
            grace_ended_at=row.grace_ended_at,
        )

    async def _get_or_raise(self, subscription_id: int) -> Subscription:
        row = await self.session.get(Subscription, subscription_id)
        if row is None:
            raise NotFoundError(
                f"Subscription '{subscription_id}' not found", code=ErrorCode.SUBSCRIPTION_NOT_FOUND
            )
        return row

    async def get_by_id(self, subscription_id: int) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def list_all(self) -> list[SubscriptionDto]:
        result = await self.session.execute(select(Subscription).order_by(Subscription.id))
        rows = result.scalars().all()
        entitlements_by_subscription = await self._load_entitlements([row.id for row in rows])
        return [self._to_dto(row, entitlements_by_subscription.get(row.id, [])) for row in rows]

    async def list_paginated(
        self,
        *,
        status: Optional[SubscriptionStatus] = None,
        search: Optional[str] = None,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        hide_staff_from: Optional[int] = None,
    ) -> tuple[list[SubscriptionDto], int]:
        # status=None means the live subscriptions: DELETED rows are routine
        # history (trial->paid conversions, plan changes), asked for explicitly
        filters: list[ColumnElement[bool]] = []
        if status is None:
            filters.append(Subscription.status != SubscriptionStatus.DELETED)
        else:
            filters.append(Subscription.status == status)
        if user_id is not None:
            filters.append(Subscription.user_id == user_id)
        if hide_staff_from is not None:
            filters.append(visible_rows(Subscription.user_id, hide_staff_from))
        if search:
            search_filters: list[ColumnElement[bool]] = [
                Subscription.tag.icontains(search, autoescape=True),
                Subscription.plan_snapshot["name"].astext.icontains(search, autoescape=True),
            ]
            # id/user_id are Integer; a longer digit string would overflow asyncpg
            if search.isascii() and search.isdigit() and int(search) <= INT32_MAX:
                search_filters += [
                    Subscription.id == int(search),
                    Subscription.user_id == int(search),
                ]
            filters.append(or_(*search_filters))

        count_stmt = select(func.count()).select_from(Subscription).where(*filters)
        total = await self.session.scalar(count_stmt)

        # newest first; one user's list puts the working ones (active, limited) on top
        order: list[ColumnElement[Any]] = [Subscription.id.desc()]
        if user_id is not None:
            order.insert(0, Subscription.status.notin_(_LIVE_STATUSES))
        stmt = (
            select(Subscription)
            .where(*filters)
            .order_by(*order)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await self.session.scalars(stmt)).all()
        entitlements_by_subscription = await self._load_entitlements([row.id for row in rows])
        return [
            self._to_dto(row, entitlements_by_subscription.get(row.id, [])) for row in rows
        ], int(total or 0)

    async def get_active_by_user(self, user_id: int) -> list[SubscriptionDto]:
        result = await self.session.execute(
            select(Subscription)
            .where(Subscription.user_id == user_id, Subscription.status.in_(OWNED_STATUSES))
            .order_by(Subscription.id)
        )
        rows = result.scalars().all()
        entitlements_by_subscription = await self._load_entitlements([row.id for row in rows])
        return [self._to_dto(row, entitlements_by_subscription.get(row.id, [])) for row in rows]

    async def get_live_by_user(self, user_id: int) -> list[SubscriptionDto]:
        # every row that still has a panel identity behind it, EXPIRED included
        result = await self.session.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status != SubscriptionStatus.DELETED,
            )
            .order_by(Subscription.id)
        )
        rows = result.scalars().all()
        entitlements_by_subscription = await self._load_entitlements([row.id for row in rows])
        return [self._to_dto(row, entitlements_by_subscription.get(row.id, [])) for row in rows]

    async def has_any_subscription(
        self,
        user_id: int,
        *,
        include_trial: bool = True,
        created_before: Optional[datetime] = None,
    ) -> bool:
        # any row in any status: "has this user ever purchased", for the
        # NEW/EXISTING availability of plans and promocodes
        stmt = select(func.count()).select_from(Subscription).where(Subscription.user_id == user_id)
        if not include_trial:
            stmt = stmt.where(Subscription.is_trial.is_(False))
        if created_before is not None:
            # an imported row is as old as its panel user, not the import
            stmt = stmt.where(
                func.least(Subscription.created_at, Subscription.panel_created_at) < created_before
            )
        count = await self.session.scalar(stmt)
        return bool(count)

    async def has_paid_subscription_before(
        self, user_id: int, subscription_id: Optional[int] = None
    ) -> bool:
        """Whether the user has a paid subscription created before subscription_id,
        if given: "is this the first paid one", which has_any_subscription can't
        answer once this one exists. A free one (100% discount, promocode grant)
        doesn't count."""
        price_paid = Subscription.plan_snapshot["price_paid"].astext.cast(Numeric)
        conditions = [
            Subscription.user_id == user_id,
            Subscription.is_trial.is_(False),
            price_paid > 0,
        ]
        if subscription_id is not None:
            # ids come from one sequence, so their order is creation order
            conditions.append(Subscription.id < subscription_id)
        stmt = select(func.count()).select_from(Subscription).where(*conditions)
        return bool(await self.session.scalar(stmt))

    async def list_linked_remnawave_user_ids(self) -> set[int]:
        """Panel users some subscription still stands for (anything but DELETED)."""
        result = await self.session.scalars(
            select(Subscription.remnawave_user_id).where(
                Subscription.status != SubscriptionStatus.DELETED
            )
        )
        return set(result)

    async def list_plan_names(self, subscription_ids: list[int]) -> dict[int, str]:
        if not subscription_ids:
            return {}
        result = await self.session.execute(
            select(Subscription.id, Subscription.plan_snapshot["name"].astext).where(
                in_ids(Subscription.id, subscription_ids)
            )
        )
        return dict(result.tuples().all())

    async def list_non_terminal(self) -> list[SubscriptionDto]:
        # recently EXPIRED rows too: a renewal that reached the panel but not
        # our commit leaves the row EXPIRED until reconcile pulls it in
        result = await self.session.execute(
            select(Subscription)
            .where(
                or_(
                    Subscription.status.in_(OWNED_STATUSES),
                    and_(
                        Subscription.status == SubscriptionStatus.EXPIRED,
                        Subscription.expire_at > datetime_now() - RECONCILE_EXPIRED_WINDOW,
                    ),
                )
            )
            .order_by(Subscription.id)
        )
        rows = result.scalars().all()
        entitlements_by_subscription = await self._load_entitlements([row.id for row in rows])
        return [self._to_dto(row, entitlements_by_subscription.get(row.id, [])) for row in rows]

    async def get_by_id_for_update(self, subscription_id: int) -> SubscriptionDto:
        result = await self.session.execute(
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                f"Subscription '{subscription_id}' not found", code=ErrorCode.SUBSCRIPTION_NOT_FOUND
            )
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def get_live_by_user_for_update(self, user_id: int) -> list[SubscriptionDto]:
        result = await self.session.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status != SubscriptionStatus.DELETED,
            )
            .order_by(Subscription.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        rows = result.scalars().all()
        entitlements_by_subscription = await self._load_entitlements([row.id for row in rows])
        return [self._to_dto(row, entitlements_by_subscription.get(row.id, [])) for row in rows]

    async def find_by_remnawave_user_id(self, remnawave_user_id: int) -> Optional[SubscriptionDto]:
        result = await self.session.execute(
            select(Subscription)
            .where(Subscription.remnawave_user_id == remnawave_user_id)
            .order_by(Subscription.status == SubscriptionStatus.DELETED, Subscription.id.desc())
            .limit(1)
            .execution_options(populate_existing=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def find_by_remnawave_user_id_for_update(
        self, remnawave_user_id: int
    ) -> Optional[SubscriptionDto]:
        result = await self.session.execute(
            select(Subscription)
            .where(Subscription.remnawave_user_id == remnawave_user_id)
            .order_by(Subscription.status == SubscriptionStatus.DELETED, Subscription.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def acquire_purchase_cap_lock(self, user_id: int) -> None:
        """Serializes one user's purchases until the transaction ends. FOR UPDATE
        locks only existing rows, so two purchases could otherwise both read an
        under-cap count and both insert."""
        await self.session.execute(
            select(func.pg_advisory_xact_lock(_SUBSCRIPTION_PURCHASE_CAP_LOCK_KEY, user_id))
        )

    async def create(self, data: CreateSubscriptionDto) -> SubscriptionDto:
        row = Subscription(
            user_id=data.user_id,
            remnawave_user_id=data.remnawave_user_id,
            status=data.status,
            is_trial=data.is_trial,
            traffic_limit=data.traffic_limit,
            device_limit=data.device_limit,
            traffic_limit_strategy=data.traffic_limit_strategy,
            tag=data.tag,
            internal_squads=data.internal_squads,
            external_squad=data.external_squad,
            expire_at=data.expire_at,
            url=data.url,
            plan_snapshot=data.plan_snapshot.to_json(),
            panel_created_at=data.panel_created_at,
            term_anchor_day=data.term_anchor_day,
            device_single_reset_at=None,
            device_all_reset_at=None,
            link_reset_at=None,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return self._to_dto(row, [])

    async def update(self, data: UpdateSubscriptionDto) -> SubscriptionDto:
        # local row only; UpdateSubscriptionUseCase pushes it to the panel
        row = await self._get_or_raise(data.subscription_id)

        apply_partial_update(row, data, skip=frozenset({"subscription_id", "cleared"}))
        for name in data.cleared:
            setattr(row, name, None)

        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def addon_grant_exists(self, idempotency_key: str) -> bool:
        return await self.session.get(AddOnGrantIdempotency, idempotency_key) is not None

    async def grant_entitlement(
        self,
        *,
        subscription_id: int,
        addon_id: Optional[int],
        addon_type: AddOnType,
        amount: int,
        idempotency_key: Optional[str] = None,
    ) -> SubscriptionDto:
        await self._get_or_raise(subscription_id)  # 404s before touching AddOnEntitlement

        if idempotency_key is not None:
            existing = await self.session.get(AddOnGrantIdempotency, idempotency_key)
            if existing is not None:
                return await self.get_by_id(subscription_id)

        entitlement = AddOnEntitlement(
            subscription_id=subscription_id,
            addon_id=addon_id,
            type=addon_type,
            amount=amount,
            granted_at=datetime_now(),
        )
        self.session.add(entitlement)
        if idempotency_key is not None:
            self.session.add(AddOnGrantIdempotency(key=idempotency_key))

        await self.session.flush()
        return await self.get_by_id(subscription_id)

    async def apply_panel_config(self, data: SubscriptionConfigUpdateDto) -> SubscriptionDto:
        row = await self._get_or_raise(data.subscription_id)
        row.url = data.url
        row.traffic_limit = data.traffic_limit
        row.device_limit = data.device_limit
        row.traffic_limit_strategy = data.traffic_limit_strategy
        row.tag = data.tag
        row.internal_squads = list(data.internal_squads)
        row.external_squad = data.external_squad
        if data.removed_addon_types:
            await self.session.execute(
                delete(AddOnEntitlement).where(
                    AddOnEntitlement.subscription_id == data.subscription_id,
                    AddOnEntitlement.type.in_(data.removed_addon_types),
                )
            )
        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def apply_plan_terms(self, subscription_id: int, plan: PlanSnapshot) -> SubscriptionDto:
        # local row only; the renewal pushed the same terms to the panel first
        row = await self._get_or_raise(subscription_id)
        row.traffic_limit = plan.traffic_limit
        row.device_limit = plan.device_limit
        row.traffic_limit_strategy = plan.traffic_limit_strategy
        row.internal_squads = list(plan.internal_squads)
        row.external_squad = plan.external_squad
        sold = PlanSnapshot.from_json(dict(row.plan_snapshot))
        row.plan_snapshot = replace(
            sold,
            traffic_limit=plan.traffic_limit,
            device_limit=plan.device_limit,
            traffic_limit_strategy=plan.traffic_limit_strategy,
            internal_squads=list(plan.internal_squads),
            external_squad=plan.external_squad,
        ).to_json()

        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def update_squads(self, data: UpdateSubscriptionSquadsDto) -> SubscriptionDto:
        # local row only; UpdateSubscriptionSquadsUseCase pushes it to the panel
        row = await self._get_or_raise(data.subscription_id)
        row.internal_squads = data.internal_squads
        row.external_squad = data.external_squad

        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def mark_deleted(self, subscription_id: int) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        state = self._to_state(row).mark_deleted()
        row.status = state.status
        row.grace_until = state.grace_until
        row.grace_ended_at = state.grace_ended_at
        row.grace_started_at = None

        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def set_reset_at(
        self, subscription_id: int, column: ResetColumn, value: Optional[datetime]
    ) -> None:
        row = await self._get_or_raise(subscription_id)
        setattr(row, column, value)
        await self.session.flush()

    async def set_paused_by_channel_leave(self, subscription_id: int, paused: bool) -> None:
        row = await self._get_or_raise(subscription_id)
        row.paused_by_channel_leave = paused
        await self.session.flush()

    async def set_url(self, subscription_id: int, url: str) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        row.url = url
        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def relink_panel_user(
        self, subscription_id: int, remnawave_user_id: int, url: str
    ) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        row.remnawave_user_id = remnawave_user_id
        row.url = url
        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def set_tag(self, subscription_id: int, tag: Optional[str]) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        row.tag = tag
        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def set_grace_until(
        self, subscription_id: int, value: Optional[datetime]
    ) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        row.grace_until = value
        # a new grace, or none: either way not entered yet
        row.grace_started_at = None
        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def end_grace(self, subscription_id: int) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        row.grace_ended_at = row.grace_until
        row.grace_until = None
        row.grace_started_at = None
        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def give_up_grace(self, subscription_id: int) -> None:
        """Closes this expiry to grace, as if a grace of it had ended at once."""
        row = await self._get_or_raise(subscription_id)
        row.grace_ended_at = row.expire_at
        row.grace_until = None
        row.grace_started_at = None
        await self.session.flush()

    async def mark_grace_started(self, subscription_id: int, at: datetime) -> None:
        row = await self._get_or_raise(subscription_id)
        row.grace_started_at = at
        await self.session.flush()

    async def carries_grace(self, remnawave_user_id: int) -> bool:
        """In grace, or its expiry is one a grace already ended."""
        return bool(
            await self.session.scalar(
                select(
                    select(Subscription.id)
                    .where(
                        Subscription.remnawave_user_id == remnawave_user_id,
                        Subscription.status != SubscriptionStatus.DELETED,
                        or_(
                            Subscription.grace_until.is_not(None),
                            Subscription.expire_at <= Subscription.grace_ended_at,
                        ),
                    )
                    .exists()
                )
            )
        )

    async def _list_where(
        self, *conditions: Any, limit: int, order: Any = Subscription.grace_attempted_at
    ) -> list[SubscriptionDto]:
        result = await self.session.execute(
            select(Subscription)
            .where(*conditions)
            .order_by(order.asc().nulls_first(), Subscription.id)
            .limit(limit)
            # the marks above are bulk updates: a row already in the session keeps old values
            .execution_options(populate_existing=True)
        )
        rows = result.scalars().all()
        entitlements_by_subscription = await self._load_entitlements([row.id for row in rows])
        return [self._to_dto(row, entitlements_by_subscription.get(row.id, [])) for row in rows]

    async def list_grace_entry_candidates(self, now: datetime, limit: int) -> list[SubscriptionDto]:
        return await self._list_where(
            or_(
                Subscription.status == SubscriptionStatus.EXPIRED,
                # its expiry webhook was lost (`grace.grace_entry_allowed`)
                and_(
                    Subscription.status.in_(LIVE_STATUSES),
                    Subscription.expire_at <= now,
                ),
            ),
            Subscription.grace_until.is_(None),
            Subscription.expire_at > now - GRACE_ENTRY_WINDOW,
            or_(
                Subscription.grace_ended_at.is_(None),
                Subscription.expire_at > Subscription.grace_ended_at,
            ),
            # trials never get grace and would crowd the batch out for the whole window
            Subscription.is_trial.is_(False),
            # nor plans closed to renewal or with no paid duration (`grace.can_renew`)
            select(Plan.id)
            .where(
                Plan.id == Subscription.plan_snapshot["plan_id"].as_integer(),
                Plan.allow_renewal.is_(True),
                or_(
                    Plan.is_trial.is_(True),
                    select(PlanDuration.id)
                    .where(PlanDuration.plan_id == Plan.id, PlanDuration.base_price > 0)
                    .exists(),
                ),
            )
            .exists(),
            limit=limit,
        )

    async def list_grace_unstarted(self, now: datetime, limit: int) -> list[SubscriptionDto]:
        """Claimed, but the entry that claimed it never finished."""
        return await self._list_where(
            Subscription.grace_until > now,
            Subscription.grace_started_at.is_(None),
            Subscription.status != SubscriptionStatus.DELETED,
            limit=limit,
        )

    async def list_grace_ended(self, now: datetime, limit: int) -> list[SubscriptionDto]:
        return await self._list_where(
            Subscription.grace_until <= now,
            Subscription.status != SubscriptionStatus.DELETED,
            limit=limit,
        )

    async def list_panel_restore_due(self, limit: int) -> list[SubscriptionDto]:
        return await self._list_where(
            Subscription.panel_restore_due_at.is_not(None),
            limit=limit,
            order=Subscription.panel_restore_due_at,
        )

    async def mark_panel_restore_due(self, subscription_id: int, now: datetime) -> None:
        await self.session.execute(
            update(Subscription)
            .where(Subscription.id == subscription_id)
            # bookkeeping, not a change to the subscription
            .values(panel_restore_due_at=now, updated_at=Subscription.updated_at)
        )

    async def clear_panel_restore_due(self, subscription_id: int, marked_at: datetime) -> None:
        """Only the mark that was pushed: one set again meanwhile stays for the next run."""
        await self.session.execute(
            update(Subscription)
            .where(
                Subscription.id == subscription_id,
                Subscription.panel_restore_due_at == marked_at,
            )
            .values(panel_restore_due_at=None, updated_at=Subscription.updated_at)
        )

    async def mark_grace_attempted(self, subscription_ids: list[int], now: datetime) -> None:
        await self.session.execute(
            update(Subscription)
            .where(in_ids(Subscription.id, subscription_ids))
            # bookkeeping, not a change to the subscription
            .values(grace_attempted_at=now, updated_at=Subscription.updated_at)
        )

    async def set_first_connected_at(self, remnawave_user_id: int, at: datetime) -> None:
        await self.session.execute(
            update(Subscription)
            .where(
                Subscription.remnawave_user_id == remnawave_user_id,
                Subscription.status != SubscriptionStatus.DELETED,
                Subscription.first_connected_at.is_(None),
            )
            .values(first_connected_at=at)
        )

    async def set_panel_created_at(self, remnawave_user_id: int, at: datetime) -> None:
        await self.session.execute(
            update(Subscription)
            .where(
                Subscription.remnawave_user_id == remnawave_user_id,
                Subscription.status != SubscriptionStatus.DELETED,
                Subscription.panel_created_at.is_distinct_from(at),
            )
            .values(panel_created_at=at)
        )

    async def set_term_anchor_day(
        self, subscription_id: int, day: Optional[int]
    ) -> SubscriptionDto:
        row = await self._get_or_raise(subscription_id)
        row.term_anchor_day = day
        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))

    async def set_panel_created_at_many(self, created_at: dict[int, datetime]) -> None:
        """One executemany for a whole panel sync, keyed by remnawave_user_id."""
        table = cast(Table, Subscription.__table__)
        await self.session.execute(
            update(table)
            .where(
                table.c.remnawave_user_id == bindparam("panel_id"),
                table.c.status != SubscriptionStatus.DELETED,
                table.c.panel_created_at.is_distinct_from(bindparam("at")),
            )
            .values(panel_created_at=bindparam("at")),
            [{"panel_id": panel_id, "at": at} for panel_id, at in created_at.items()],
        )

    async def apply_state(self, state: SubscriptionState) -> SubscriptionDto:
        row = await self._get_or_raise(state.id)
        row.status = state.status
        row.expire_at = state.expire_at
        row.plan_snapshot = state.plan_snapshot.to_json()
        # only SubscriptionLifecycleService calls apply_state, and neither of
        # its methods changes these fields, so this rewrites the same values
        row.traffic_limit = state.traffic_limit
        row.device_limit = state.device_limit
        row.traffic_limit_strategy = state.traffic_limit_strategy
        row.internal_squads = state.internal_squads
        row.external_squad = state.external_squad
        row.is_trial = state.is_trial

        await self.session.flush()
        await self.session.refresh(row)
        entitlements_by_subscription = await self._load_entitlements([row.id])
        return self._to_dto(row, entitlements_by_subscription.get(row.id, []))
