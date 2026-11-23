from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.cli.legacy.panel import PanelIndex
from app.cli.legacy.report import MigrationReport
from app.cli.legacy.subscriptions import (
    convert_plan_snapshot,
    credit_segments,
    migrated_subscription_ids,
    transform_subscriptions,
)
from app.core.enums import CreditSegmentKind, Currency, SubscriptionStatus
from app.modules.subscriptions.dto import PlanSnapshot

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SNAPSHOT: dict[str, Any] = {
    "id": 3,
    "tag": "PRO",
    "name": "Pro",
    "type": "BOTH",
    "duration": 30,
    "is_trial": False,
    "device_limit": 3,
    "traffic_limit": 100,
    "external_squad": None,
    "internal_squads": ["11111111-1111-1111-1111-111111111111"],
    "traffic_limit_strategy": "MONTH",
}
CONNECTED = datetime(2025, 12, 1, tzinfo=timezone.utc)
PANEL_CREATED = datetime(2025, 3, 31, tzinfo=timezone.utc)
INDEX = PanelIndex(
    by_username={"rs_1001": 55},
    by_telegram_id={},
    first_connected_at={55: CONNECTED},
    created_at={55: PANEL_CREATED},
)
USERS = {1: {"telegram_id": 1001}, 2: {"telegram_id": 1002}}


def _row(sub_id: int, status: str = "ACTIVE", user_id: int = 1, **extra: Any) -> dict[str, Any]:
    return {
        "id": sub_id,
        "user_id": user_id,
        "status": status,
        "is_trial": False,
        "disabled_by_channel_leave": False,
        "traffic_limit": 100,
        "device_limit": 3,
        "traffic_limit_strategy": "MONTH",
        "tag": None,
        "internal_squads": [],
        "external_squad": None,
        "expire_at": NOW,
        "url": "https://sub.example.com/zzz",
        "plan_snapshot": SNAPSHOT,
        "device_single_reset_at": None,
        "device_all_reset_at": None,
        "link_reset_at": None,
        "created_at": NOW,
        "updated_at": NOW,
        **extra,
    }


def _run(
    rows: list[dict[str, Any]],
    current: set[int],
    report: MigrationReport,
    index: PanelIndex = INDEX,
    grace_days: int = 3,
) -> list[dict[str, Any]]:
    return transform_subscriptions(
        rows,
        users=USERS,
        current_subscription_ids=current,
        index=index,
        grace_days=grace_days,
        plan_codes={3: "pro"},
        last_payments={(1, 3): (Decimal("199"), Currency.RUB)},
        anchor_currency=Currency.USD,
        report=report,
    )


def test_only_current_subscription_stays_live() -> None:
    report = MigrationReport()
    result = _run([_row(73, "DISABLED"), _row(95, "DELETED"), _row(350)], {350}, report)
    status = {item["id"]: item["status"] for item in result}
    assert status == {
        73: SubscriptionStatus.DELETED,
        95: SubscriptionStatus.DELETED,
        350: SubscriptionStatus.ACTIVE,
    }


def test_user_without_current_subscription() -> None:
    result = _run([_row(1), _row(2)], set(), MigrationReport())
    assert {item["status"] for item in result} == {SubscriptionStatus.DELETED}


def test_missing_panel_user_not_migrated() -> None:
    report = MigrationReport()
    result = _run([_row(5, user_id=2)], {5}, report)
    assert result == []
    assert report.skips and "panel user" in report.skips[0].reason
    assert migrated_subscription_ids(result) == set()


def test_remnawave_user_id_from_panel_and_flag_renamed() -> None:
    result = _run([_row(1, disabled_by_channel_leave=True)], {1}, MigrationReport())
    assert result[0]["remnawave_user_id"] == 55
    assert result[0]["paused_by_channel_leave"] is True
    assert "disabled_by_channel_leave" not in result[0]


def test_first_connection_from_panel_on_every_subscription() -> None:
    result = _run([_row(1), _row(2, "EXPIRED")], {1}, MigrationReport())
    assert [item["first_connected_at"] for item in result] == [CONNECTED, CONNECTED]


def test_panel_creation_date_on_every_subscription() -> None:
    result = _run([_row(1), _row(2, "EXPIRED")], {1}, MigrationReport())
    assert [item["panel_created_at"] for item in result] == [PANEL_CREATED, PANEL_CREATED]


def test_plan_snapshot_converted() -> None:
    result = _run([_row(1)], {1}, MigrationReport())
    snapshot = PlanSnapshot.from_json(result[0]["plan_snapshot"])
    assert snapshot.plan_id == 3
    assert snapshot.public_code == "pro"
    assert snapshot.duration_days == 30
    assert snapshot.price_paid == Decimal("199")
    assert snapshot.currency is Currency.RUB
    assert snapshot.device_limit == 3


def test_missing_plan_and_payment_fall_back_and_are_reported() -> None:
    report = MigrationReport()
    row = _row(1, plan_snapshot={**SNAPSHOT, "id": -1})
    result = _run([row], {1}, report)
    snapshot = PlanSnapshot.from_json(result[0]["plan_snapshot"])
    assert snapshot.public_code == ""
    assert (snapshot.price_paid, snapshot.currency) == (Decimal("0"), Currency.USD)
    assert any("priced at 0" in note for note in report.notes)
    assert any("empty public code" in note for note in report.notes)


def test_live_unique_per_panel_user() -> None:
    users = {1: {"telegram_id": 1001}, 3: {"telegram_id": 1001}}
    result = transform_subscriptions(
        [_row(1), _row(2, user_id=3)],
        users=users,
        current_subscription_ids={1, 2},
        index=INDEX,
        grace_days=0,
        plan_codes={3: "pro"},
        last_payments={},
        anchor_currency=Currency.USD,
        report=MigrationReport(),
    )
    live = [i["remnawave_user_id"] for i in result if i["status"] is not SubscriptionStatus.DELETED]
    assert len(live) == len(set(live)) == 1


def test_convert_plan_snapshot_shape() -> None:
    out = convert_plan_snapshot(
        SNAPSHOT, public_code="pro", price_paid=Decimal("1"), currency=Currency.USD
    )
    assert "credit_paid" not in out


def test_convert_plan_snapshot_drops_the_leading_emoji_like_the_plan() -> None:
    out = convert_plan_snapshot(
        {**SNAPSHOT, "name": "🔅ОБЫЧНЫЙ"},
        public_code="pro",
        price_paid=Decimal("1"),
        currency=Currency.USD,
    )
    assert out["name"] == "ОБЫЧНЫЙ"


def test_live_subscription_takes_panel_state() -> None:
    panel_expire = datetime(2025, 9, 1, tzinfo=timezone.utc)
    index = PanelIndex(
        by_username={"rs_1001": 55},
        status={55: SubscriptionStatus.EXPIRED},
        expire_at={55: panel_expire},
    )
    result = transform_subscriptions(
        [_row(1, "DISABLED"), _row(2)],
        users=USERS,
        current_subscription_ids={2},
        index=index,
        grace_days=0,
        plan_codes={3: "pro"},
        last_payments={},
        anchor_currency=Currency.USD,
        report=MigrationReport(),
    )
    by_id = {item["id"]: item for item in result}
    assert by_id[1]["status"] is SubscriptionStatus.DELETED
    assert by_id[1]["expire_at"] == NOW
    assert by_id[2]["status"] is SubscriptionStatus.EXPIRED
    assert by_id[2]["expire_at"] == panel_expire


def test_subscription_takes_panel_link() -> None:
    index = PanelIndex(by_username={"rs_1001": 55}, url={55: "https://sub.example.com/new"})
    result = transform_subscriptions(
        [_row(1)],
        users=USERS,
        current_subscription_ids={1},
        index=index,
        grace_days=0,
        plan_codes={3: "pro"},
        last_payments={},
        anchor_currency=Currency.USD,
        report=MigrationReport(),
    )
    assert result[0]["url"] == "https://sub.example.com/new"


def test_each_migrated_subscription_gets_its_plan_price_as_one_segment() -> None:
    expire_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    term = {"price_paid": "300", "currency": "RUB", "duration_days": 30}
    forever = {"price_paid": "5000", "currency": "USD", "duration_days": 0}

    segments = credit_segments(
        [
            {"id": 1, "expire_at": expire_at, "plan_snapshot": term},
            {"id": 2, "expire_at": expire_at, "plan_snapshot": forever},
        ]
    )

    assert segments == [
        {
            "subscription_id": 1,
            "kind": CreditSegmentKind.LEGACY,
            "paid": Decimal("300"),
            "currency": Currency.RUB,
            "seconds": 30 * 86400,
            "ends_at": expire_at,
        },
        {
            "subscription_id": 2,
            "kind": CreditSegmentKind.LEGACY,
            "paid": Decimal("5000"),
            "currency": Currency.USD,
            "seconds": 0,
            "ends_at": None,
        },
    ]


def test_an_old_unlimited_plan_gets_an_endless_segment() -> None:
    # the old version stored an unlimited term as -1 days
    expire_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    forever = {"price_paid": "0", "currency": "RUB", "duration_days": -1}

    [segment] = credit_segments([{"id": 1, "expire_at": expire_at, "plan_snapshot": forever}])

    assert segment["seconds"] == 0
    assert segment["ends_at"] is None


GRACE_UNTIL = datetime(2026, 1, 4, tzinfo=timezone.utc)
GRACE_INDEX = PanelIndex(
    by_username={"rs_1001": 55},
    status={55: SubscriptionStatus.ACTIVE},
    expire_at={55: GRACE_UNTIL},
)
GRACE_CONFIG: dict[str, Any] = {
    "traffic_limit": 1,
    "traffic_limit_strategy": "DAY",
    "tag": "GRACE",
    "internal_squads": [UUID("33333333-3333-3333-3333-333333333333")],
    "status": "ACTIVE",
}


def _panel_at(expire_at: datetime) -> PanelIndex:
    return PanelIndex(
        by_username={"rs_1001": 55},
        status={55: SubscriptionStatus.ACTIVE},
        expire_at={55: expire_at},
    )


def test_stale_grace_mark_on_a_renewed_row_is_dropped() -> None:
    # the old app left grace_until behind on admin, promocode and referral day grants
    renewed = datetime(2026, 2, 1, tzinfo=timezone.utc)
    report = MigrationReport()
    row = _row(1, grace_until=GRACE_UNTIL, expire_at=renewed, tag="PRO")
    [item] = _run([row], {1}, report, index=_panel_at(renewed))
    assert item["grace_until"] is None
    assert item["status"] is SubscriptionStatus.ACTIVE
    assert item["expire_at"] == renewed
    assert item["tag"] == "PRO"
    assert "subscriptions: 1 stale grace marks dropped" in report.notes
    assert not any("in grace" in note for note in report.notes)


def test_stale_past_grace_mark_is_dropped() -> None:
    renewed = datetime(2026, 2, 1, tzinfo=timezone.utc)
    stale = datetime(2025, 11, 1, tzinfo=timezone.utc)
    [item] = _run(
        [_row(1, grace_until=stale, expire_at=renewed)],
        {1},
        MigrationReport(),
        index=_panel_at(renewed),
    )
    assert item["grace_until"] is None
    assert item["expire_at"] == renewed


def test_subscription_in_grace_keeps_real_expiry() -> None:
    report = MigrationReport()
    row = _row(1, "EXPIRED", grace_until=GRACE_UNTIL)
    [item] = _run([row], {1}, report, index=GRACE_INDEX)
    assert item["status"] is SubscriptionStatus.EXPIRED
    assert item["expire_at"] == NOW
    assert item["grace_until"] == GRACE_UNTIL
    # entered by the old app: the new one must not enter, or announce, it again
    assert item["grace_started_at"] == NOW
    assert "subscriptions: 1 in grace" in report.notes


def test_grace_overwritten_row_gets_expiry_and_plan_config_back() -> None:
    row = _row(1, grace_until=GRACE_UNTIL, **{**GRACE_CONFIG, "expire_at": GRACE_UNTIL})
    [item] = _run([row], {1}, MigrationReport(), index=GRACE_INDEX)
    assert item["status"] is SubscriptionStatus.EXPIRED
    assert item["expire_at"] == GRACE_UNTIL - timedelta(days=3)
    assert item["grace_until"] == GRACE_UNTIL
    assert item["traffic_limit"] == 100
    assert item["traffic_limit_strategy"] == "MONTH"
    assert item["tag"] == "PRO"
    assert item["internal_squads"] == [UUID("11111111-1111-1111-1111-111111111111")]
    assert item["external_squad"] is None


def test_indefinite_grace_overwritten_row_falls_back_to_last_update() -> None:
    forever = datetime(2099, 1, 1, tzinfo=timezone.utc)
    updated = datetime(2025, 12, 20, tzinfo=timezone.utc)
    report = MigrationReport()
    row = _row(1, grace_until=forever, expire_at=forever, updated_at=updated)
    [item] = _run([row], {1}, report, index=_panel_at(forever), grace_days=0)
    assert item["expire_at"] == updated
    assert item["grace_until"] == forever
    assert any("#1: real expiry unknown" in note for note in report.notes)


def test_deleted_subscription_drops_grace() -> None:
    report = MigrationReport()
    [item] = _run([_row(1, "DELETED", grace_until=GRACE_UNTIL)], set(), report)
    assert item["status"] is SubscriptionStatus.DELETED
    assert item["grace_until"] is None
    assert not any("in grace" in note for note in report.notes)


def test_row_without_grace_column() -> None:
    [item] = _run([_row(1)], {1}, MigrationReport())
    assert item["grace_until"] is None


def test_grace_overwritten_row_with_panel_milliseconds() -> None:
    until = GRACE_UNTIL.replace(microsecond=123456)
    echoed = until.replace(microsecond=123000)
    row = _row(1, grace_until=until, expire_at=echoed, tag="GRACE")
    [item] = _run([row], {1}, MigrationReport(), index=GRACE_INDEX)
    assert item["expire_at"] == until - timedelta(days=3)
    assert item["tag"] == "PRO"


def test_indefinite_grace_overwritten_row_with_panel_milliseconds() -> None:
    forever = datetime(2099, 1, 1, 0, 0, 0, 654321, tzinfo=timezone.utc)
    updated = datetime(2025, 12, 20, tzinfo=timezone.utc)
    row = _row(
        1, grace_until=forever, expire_at=forever.replace(microsecond=654000), updated_at=updated
    )
    [item] = _run([row], {1}, MigrationReport(), index=_panel_at(forever), grace_days=0)
    assert item["expire_at"] == updated


def test_grace_row_with_an_unexpected_status_is_reported() -> None:
    report = MigrationReport()
    _run([_row(1, "LIMITED", grace_until=GRACE_UNTIL)], {1}, report, index=GRACE_INDEX)
    assert any("#1: in grace with status LIMITED" in note for note in report.notes)
