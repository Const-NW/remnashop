from datetime import timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models.event_outbox import EventOutboxEntry
from app.core.enums import TransactionStatus
from app.core.events import EventCategory
from app.core.utils.time import datetime_now
from app.modules.email import record_smtp_failure
from app.modules.event_log import EventOutboxRepository
from app.modules.overview.dto import AttentionKind
from app.modules.overview.system_health import (
    ALERT_AFTER_CHECKS,
    GATEWAY_FAILED_SPIKE,
    RESTORE_AFTER_CHECKS,
    HealthProblem,
    HealthState,
    advance,
    drop_worker_consequences,
    read_service_problems,
)
from app.modules.transactions import TransactionStatsRepository
from app.modules.transactions.repository import TransactionRepository
from app.modules.users.dto import CreateUserDto
from app.modules.users.repository import UserRepository
from tests.modules.overview.test_usecases import _paid_order_dto

_WORKER = HealthProblem(kind=AttentionKind.PROCESS_DOWN, subject="worker")
_MEMORY = HealthProblem(kind=AttentionKind.HOST_MEMORY_HIGH, value=97)


def _run(checks: list[list[HealthProblem]]) -> tuple[HealthState, list[str], list[str]]:
    state = HealthState(streaks={}, alerted={})
    degraded: list[str] = []
    restored: list[str] = []
    for problems in checks:
        transition = advance(state, problems)
        state = transition.state
        degraded += [p.key for p in transition.degraded]
        restored += transition.restored
    return state, degraded, restored


def test_alerts_once_after_the_streak_and_restores_after_good_checks() -> None:
    down = ALERT_AFTER_CHECKS[AttentionKind.PROCESS_DOWN]
    state, degraded, restored = _run([[_WORKER]] * (down + 3) + [[]] * RESTORE_AFTER_CHECKS + [[]])
    assert degraded == [_WORKER.key]
    assert restored == [_WORKER.key]
    assert state == HealthState(streaks={}, alerted={})


def test_a_good_check_resets_the_streak() -> None:
    need = ALERT_AFTER_CHECKS[AttentionKind.HOST_MEMORY_HIGH]
    _, degraded, _ = _run(([[_MEMORY]] * (need - 1) + [[]]) * 3)
    assert degraded == []


def test_a_relapse_while_recovering_keeps_the_alert_without_repeating_it() -> None:
    down = ALERT_AFTER_CHECKS[AttentionKind.PROCESS_DOWN]
    checks = [[_WORKER]] * down + [[]] * (RESTORE_AFTER_CHECKS - 1) + [[_WORKER]]
    state, degraded, restored = _run(checks + [[]] * (RESTORE_AFTER_CHECKS - 1))
    assert degraded == [_WORKER.key]
    assert restored == []
    assert _WORKER.key in state.alerted


async def test_service_problems_none_on_a_quiet_shop(
    session: AsyncSession, redis: Redis, _clean_redis: None
) -> None:
    problems = await read_service_problems(
        redis, EventOutboxRepository(session), TransactionStatsRepository(session), datetime_now()
    )
    assert problems == []


async def test_service_problems_cover_smtp_the_outbox_and_a_gateway_spike(
    session: AsyncSession,
    redis: Redis,
    _clean_redis: None,
    user_repository: UserRepository,
    plan_id: int,
) -> None:
    now = datetime_now()
    await record_smtp_failure(redis, bulk=False, at=now)
    for age in (10, 2):
        session.add(
            EventOutboxEntry(
                category=EventCategory.AUTH,
                event_type="LoginSucceededEvent",
                occurred_at=now - timedelta(minutes=age),
                payload={},
            )
        )
    user = await user_repository.create(CreateUserDto(name="Payer", telegram_id=840_201))
    transactions = TransactionRepository(session)
    for i in range(GATEWAY_FAILED_SPIKE):
        order = await transactions.create(
            _paid_order_dto(user_id=user.id, plan_id=plan_id, idempotency_key=f"health-f{i}")
        )
        await transactions.mark_status(order.id, TransactionStatus.FAILED)

    problems = await read_service_problems(
        redis, EventOutboxRepository(session), TransactionStatsRepository(session), now
    )

    assert {p.key: p.value for p in problems} == {
        "smtp_failed:main": None,
        "outbox_stuck:": 1,
        "gateway_failures:YOOKASSA": GATEWAY_FAILED_SPIKE,
    }


_OUTBOX = HealthProblem(kind=AttentionKind.OUTBOX_STUCK, value=12)


def test_a_stopped_worker_holds_back_the_outbox_alert_but_keeps_one_already_sent() -> None:
    quiet = HealthState(streaks={}, alerted={})
    assert drop_worker_consequences(quiet, [_WORKER, _OUTBOX]) == [_WORKER]
    assert drop_worker_consequences(quiet, [_OUTBOX]) == [_OUTBOX]
    sent = HealthState(streaks={}, alerted={_OUTBOX.key: 0})
    assert drop_worker_consequences(sent, [_WORKER, _OUTBOX]) == [_WORKER, _OUTBOX]
