from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Optional
from uuid import UUID

from app.cli.legacy.panel import PanelIndex, resolve_panel_id
from app.cli.legacy.plans import strip_leading_emoji
from app.cli.legacy.report import MigrationReport
from app.core.constants import TIME_1D
from app.core.enums import CreditSegmentKind, Currency, SubscriptionStatus
from app.modules.subscriptions import is_unlimited_expiry

_TABLE: Final[str] = "subscriptions"
_ECHO_TOLERANCE: Final[timedelta] = timedelta(seconds=1)
_GRACE_STATUSES: Final[frozenset[str]] = frozenset({"ACTIVE", "EXPIRED"})
_COPIED: Final[tuple[str, ...]] = (
    "id",
    "user_id",
    "status",
    "is_trial",
    "traffic_limit",
    "device_limit",
    "traffic_limit_strategy",
    "tag",
    "internal_squads",
    "external_squad",
    "expire_at",
    "url",
    "device_single_reset_at",
    "device_all_reset_at",
    "link_reset_at",
    "grace_until",
    "created_at",
    "updated_at",
)


def convert_plan_snapshot(
    old: Mapping[str, Any], *, public_code: str, price_paid: Decimal, currency: Currency
) -> dict[str, Any]:
    duration = int(old.get("duration") or 0)
    return {
        "plan_id": old["id"],
        "public_code": public_code,
        # the same name the migrated plan got, so reports group old and new orders together
        "name": strip_leading_emoji(old["name"]),
        "price_paid": str(price_paid),
        "currency": currency.value,
        "duration_days": duration,
        "traffic_limit": old["traffic_limit"],
        "device_limit": old["device_limit"],
        "traffic_limit_strategy": str(old["traffic_limit_strategy"]),
        "is_trial": bool(old.get("is_trial")),
        "internal_squads": [str(squad) for squad in old.get("internal_squads") or []],
        "external_squad": str(old["external_squad"]) if old.get("external_squad") else None,
        "tag": old.get("tag"),
    }


def credit_segments(subscriptions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One segment per subscription: the last payment for its plan over the plan's
    term, ending at its expiry; an unlimited plan has no end."""
    result: list[dict[str, Any]] = []
    for sub in subscriptions:
        snapshot = sub["plan_snapshot"]
        days = int(snapshot["duration_days"])
        result.append(
            {
                "subscription_id": sub["id"],
                "kind": CreditSegmentKind.LEGACY,
                "paid": Decimal(snapshot["price_paid"]),
                "currency": Currency(snapshot["currency"]),
                "seconds": max(days, 0) * TIME_1D,
                "ends_at": sub["expire_at"] if days > 0 else None,
            }
        )
    return result


def migrated_subscription_ids(result: Sequence[Mapping[str, Any]]) -> set[int]:
    return {int(item["id"]) for item in result}


def _claim_live(
    status: SubscriptionStatus,
    sub_id: int,
    panel_id: int,
    live_panel_ids: dict[int, int],
    report: MigrationReport,
) -> SubscriptionStatus:
    if panel_id in live_panel_ids:
        report.notes.append(
            f"subscriptions #{sub_id}: deleted, panel user {panel_id} "
            f"is already used by #{live_panel_ids[panel_id]}"
        )
        return SubscriptionStatus.DELETED
    live_panel_ids[panel_id] = sub_id
    return status


def _holds_grace(panel_expire_at: Optional[datetime], grace_until: datetime) -> bool:
    # the panel echoes milliseconds, grace_until keeps microseconds
    return panel_expire_at is not None and abs(panel_expire_at - grace_until) <= _ECHO_TOLERANCE


def _grace_terms(
    row: Mapping[str, Any],
    status: SubscriptionStatus,
    panel_expire_at: Optional[datetime],
    grace_days: int,
    report: MigrationReport,
) -> dict[str, Any]:
    """The old app's sync copied the panel's grace config, expiry included, into the
    row; the expiry is rebuilt from the grace length and the rest from the plan snapshot."""
    grace_until = row.get("grace_until")
    if grace_until is None or status is SubscriptionStatus.DELETED:
        return {"grace_until": None}
    # the old app cleared grace_until only on a paid renewal and at grace end: day
    # grants and panel extensions left it on a renewed row the panel no longer holds
    if not _holds_grace(panel_expire_at, grace_until):
        return {"grace_until": None, "stale_grace": True}
    # the panel holds the grace config and the grace end, never the row's
    terms = {"status": SubscriptionStatus.EXPIRED, "grace_until": grace_until}
    if row["status"] not in _GRACE_STATUSES:
        report.notes.append(f"subscriptions #{row['id']}: in grace with status {row['status']}")
    if row["expire_at"] < grace_until - _ECHO_TOLERANCE:
        return {**terms, "expire_at": row["expire_at"]}
    if grace_days > 0 and not is_unlimited_expiry(grace_until):
        expire_at = grace_until - timedelta(days=grace_days)
    else:
        expire_at = row["updated_at"]
        report.notes.append(
            f"subscriptions #{row['id']}: real expiry unknown, set to the last update {expire_at}"
        )
    snapshot = row["plan_snapshot"]
    external_squad = snapshot.get("external_squad")
    return {
        **terms,
        "expire_at": expire_at,
        "traffic_limit": snapshot["traffic_limit"],
        "traffic_limit_strategy": snapshot["traffic_limit_strategy"],
        "tag": snapshot.get("tag"),
        "internal_squads": [UUID(str(squad)) for squad in snapshot.get("internal_squads") or []],
        "external_squad": UUID(str(external_squad)) if external_squad else None,
    }


def _add_notes(
    report: MigrationReport,
    demoted: int,
    unpriced: int,
    unknown_plans: set[int],
    in_grace: int,
    stale_grace: int,
) -> None:
    if demoted:
        report.notes.append(f"subscriptions: {demoted} non-current subscriptions set to DELETED")
    if unpriced:
        report.notes.append(
            f"subscriptions: {unpriced} snapshots without a completed payment priced at 0"
        )
    if unknown_plans:
        report.notes.append(
            f"subscriptions: snapshot plans {sorted(unknown_plans)} not migrated, empty public code"
        )
    if in_grace:
        report.notes.append(f"subscriptions: {in_grace} in grace")
    if stale_grace:
        report.notes.append(f"subscriptions: {stale_grace} stale grace marks dropped")


def transform_subscriptions(
    rows: Sequence[Mapping[str, Any]],
    *,
    users: Mapping[int, Mapping[str, Any]],
    current_subscription_ids: set[int],
    index: PanelIndex,
    grace_days: int,
    plan_codes: Mapping[int, str],
    last_payments: Mapping[tuple[int, int], tuple[Decimal, Currency]],
    anchor_currency: Currency,
    report: MigrationReport,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    live_panel_ids: dict[int, int] = {}
    demoted = 0
    unpriced = 0
    unknown_plans: set[int] = set()
    in_grace = 0
    stale_grace = 0

    for row in sorted(rows, key=lambda r: int(r["id"])):
        sub_id = int(row["id"])
        user_id = int(row["user_id"])
        user = users.get(user_id)
        if user is None:
            report.skip(_TABLE, str(sub_id), f"user {user_id} not migrated")
            continue
        panel_id, reason = resolve_panel_id(
            url=row.get("url"), telegram_id=user.get("telegram_id"), user_id=user_id, index=index
        )
        if panel_id is None:
            report.skip(_TABLE, str(sub_id), f"panel user not found: {reason}")
            continue

        status = SubscriptionStatus[str(row["status"])]
        if status is not SubscriptionStatus.DELETED:
            if sub_id not in current_subscription_ids:
                status, demoted = SubscriptionStatus.DELETED, demoted + 1
            else:
                status = _claim_live(status, sub_id, panel_id, live_panel_ids, report)

        old_snapshot = row["plan_snapshot"]
        plan_id = int(old_snapshot["id"])
        public_code = plan_codes.get(plan_id)
        if public_code is None:
            public_code = ""
            unknown_plans.add(plan_id)
        paid = last_payments.get((user_id, plan_id))
        if paid is None:
            paid, unpriced = (Decimal("0"), anchor_currency), unpriced + 1
        price, currency = paid

        item = {column: row.get(column) for column in _COPIED}
        item.update(
            status=status,
            remnawave_user_id=panel_id,
            first_connected_at=index.first_connected_at.get(panel_id),
            panel_created_at=index.created_at.get(panel_id),
            paused_by_channel_leave=bool(row.get("disabled_by_channel_leave")),
            plan_snapshot=convert_plan_snapshot(
                old_snapshot, public_code=public_code, price_paid=price, currency=currency
            ),
        )
        # the old link may name a revoked shortUuid when the user was matched by username
        if panel_id in index.url:
            item.update(url=index.url[panel_id])
        if status is not SubscriptionStatus.DELETED and panel_id in index.status:
            item.update(status=index.status[panel_id], expire_at=index.expire_at[panel_id])
        grace = _grace_terms(row, status, index.expire_at.get(panel_id), grace_days, report)
        stale_grace += bool(grace.pop("stale_grace", False))
        item.update(grace)
        # entered by the old app: the sweep must not finish the entry again
        item["grace_started_at"] = item["expire_at"] if item["grace_until"] is not None else None
        in_grace += item["grace_until"] is not None
        result.append(item)

    report.count(_TABLE, len(rows), len(result))
    _add_notes(report, demoted, unpriced, unknown_plans, in_grace, stale_grace)
    return result
