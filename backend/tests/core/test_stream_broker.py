import asyncio
import gc
import uuid
from collections.abc import AsyncIterator

import pytest
from loguru import logger
from redis.asyncio import Redis
from taskiq import AckableMessage

from app.core.config import AppConfig
from app.core.stream_broker import (
    LEGACY_LIST_QUEUE,
    MAX_DELIVERIES,
    MAX_HOLD_S,
    REDELIVER_AFTER_MS,
    AckedStreamBroker,
)


@pytest.fixture
async def stream_broker(config: AppConfig, redis: Redis) -> AsyncIterator[AckedStreamBroker]:
    stream = f"test-tasks-{uuid.uuid4().hex}"
    broker = AckedStreamBroker(config.redis.dsn, queue_name=stream, xread_block=100)

    @broker.task(task_name="ping")
    async def ping() -> None:
        pass

    await broker.startup()
    yield broker
    await broker.shutdown()
    await redis.delete(stream)


async def _next(broker: AckedStreamBroker) -> AckableMessage:
    listen = broker.listen()
    message = await anext(listen)
    await listen.aclose()
    return message


async def test_a_message_stays_pending_until_acked(stream_broker: AckedStreamBroker) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]

    message = await _next(stream_broker)
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        assert (await redis.xpending(stream_broker.queue_name, "taskiq"))["pending"] == 1
        await message.ack()  # type: ignore[misc]
        assert (await redis.xpending(stream_broker.queue_name, "taskiq"))["pending"] == 0


async def test_an_unknown_task_is_acked_and_skipped(stream_broker: AckedStreamBroker) -> None:
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await redis.xadd(stream_broker.queue_name, {b"data": b"not a task"})
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]

    message = await _next(stream_broker)

    assert stream_broker.formatter.loads(message.data).task_name == "ping"
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        assert (await redis.xpending(stream_broker.queue_name, "taskiq"))["pending"] == 1


async def test_a_held_message_is_renewed_so_no_one_else_claims_it(
    stream_broker: AckedStreamBroker,
) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]
    running = await _next(stream_broker)  # noqa: F841 -- a task still running holds it
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await redis.xclaim(  # ages it as a long task would
            stream_broker.queue_name,
            "taskiq",
            stream_broker.consumer_name,
            min_idle_time=0,
            message_ids=list(stream_broker._held),
            idle=10 * 60 * 1000,
            justid=True,
        )

        await stream_broker._renew_held()

        detail = await redis.xpending_range(
            stream_broker.queue_name, "taskiq", min="-", max="+", count=10
        )
    assert detail[0]["time_since_delivered"] < 60_000


async def test_acked_messages_are_trimmed_and_pending_ones_kept(
    stream_broker: AckedStreamBroker,
) -> None:
    ping = stream_broker.find_task("ping")
    await ping.kiq()  # type: ignore[union-attr]
    await ping.kiq()  # type: ignore[union-attr]
    first = await _next(stream_broker)
    await first.ack()  # type: ignore[misc]
    # the second is read, not acked: a task still running

    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await stream_broker._trim_acked()
        assert await redis.xlen(stream_broker.queue_name) >= 1
        entries = await redis.xrange(stream_broker.queue_name)
        pending = await redis.xpending(stream_broker.queue_name, "taskiq")
    assert entries[0][0] == pending["min"]


async def test_the_old_list_queue_moves_to_the_stream_oldest_first(
    stream_broker: AckedStreamBroker,
) -> None:
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await redis.delete(LEGACY_LIST_QUEUE)
        await redis.lpush(LEGACY_LIST_QUEUE, b"first", b"second")  # type: ignore[misc]

        await stream_broker._drain_legacy_list()

        entries = await redis.xrange(stream_broker.queue_name)
        assert [fields[b"data"] for _, fields in entries] == [b"first", b"second"]
        assert await redis.exists(LEGACY_LIST_QUEUE) == 0


async def test_a_whole_read_batch_is_held_before_its_tail_is_handed_out(
    stream_broker: AckedStreamBroker,
) -> None:
    ping = stream_broker.find_task("ping")
    for _ in range(3):
        await ping.kiq()  # type: ignore[union-attr]

    first = await _next(stream_broker)  # noqa: F841 -- still waiting for a slot

    assert len(stream_broker._held) == 3


async def test_a_failed_ack_is_retried_and_the_message_held_meanwhile(
    stream_broker: AckedStreamBroker, monkeypatch: pytest.MonkeyPatch
) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]
    message = await _next(stream_broker)

    async def _refuse(*args: object, **kwargs: object) -> None:
        raise ConnectionError("redis away")

    with monkeypatch.context() as patched:
        patched.setattr(Redis, "xack", _refuse)
        # loguru's traceback walk would keep the test frame's locals, message included
        patched.setattr(logger, "exception", lambda *_: None)
        await message.ack()  # type: ignore[misc]
    # the receiver lets go of it once the task is done
    del message
    gc.collect()
    assert len(stream_broker._held) == 1

    await stream_broker._retry_failed_acks()

    assert stream_broker._held == {}
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        assert (await redis.xpending(stream_broker.queue_name, "taskiq"))["pending"] == 0


async def test_a_message_dropped_unacked_is_no_longer_held(
    stream_broker: AckedStreamBroker,
) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]
    message = await _next(stream_broker)
    assert len(stream_broker._held) == 1

    del message
    gc.collect()

    assert stream_broker._held == {}


async def _age(broker: AckedStreamBroker, consumer: str, *, retrycount: int = 1) -> None:
    async with Redis(connection_pool=broker.connection_pool) as redis:
        pending = await redis.xpending_range(broker.queue_name, "taskiq", "-", "+", 10)
        await redis.xclaim(
            broker.queue_name,
            "taskiq",
            consumer,
            min_idle_time=0,
            message_ids=[entry["message_id"] for entry in pending],
            idle=REDELIVER_AFTER_MS + 1000,
            retrycount=retrycount,
            justid=True,
        )


async def test_a_dead_consumers_message_goes_to_a_live_one(
    stream_broker: AckedStreamBroker,
) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await redis.xreadgroup("taskiq", "dead", {stream_broker.queue_name: ">"})
    await _age(stream_broker, "dead")

    message = await _next(stream_broker)

    assert stream_broker.formatter.loads(message.data).task_name == "ping"


async def test_a_message_delivered_too_often_is_given_up(
    stream_broker: AckedStreamBroker,
) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await redis.xreadgroup("taskiq", "dead", {stream_broker.queue_name: ">"})
    # it took every process that ran it down
    await _age(stream_broker, "dead", retrycount=MAX_DELIVERIES)
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]

    message = await _next(stream_broker)

    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        pending = await redis.xpending(stream_broker.queue_name, "taskiq")
    # only the fresh one is handed out and pending; the worn one is acked
    assert pending["pending"] == 1
    assert pending["min"] in stream_broker._held
    assert message.data


async def test_a_message_is_still_handed_out_on_its_last_allowed_delivery(
    stream_broker: AckedStreamBroker,
) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await redis.xreadgroup("taskiq", "dead", {stream_broker.queue_name: ">"})
    # the reclaim below makes it the MAX_DELIVERIES-th delivery
    await _age(stream_broker, "dead", retrycount=MAX_DELIVERIES - 1)

    message = await _next(stream_broker)

    assert stream_broker.formatter.loads(message.data).task_name == "ping"


async def test_a_message_held_past_the_ceiling_is_given_up_as_hung(
    stream_broker: AckedStreamBroker,
) -> None:
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]
    running = await _next(stream_broker)  # noqa: F841 -- a task that never returns
    message_id = next(iter(stream_broker._held))
    stream_broker._held[message_id] -= MAX_HOLD_S + 1

    await stream_broker._renew_held()

    assert stream_broker._held == {}
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        # acked: nobody reruns it and the trim moves past it
        assert (await redis.xpending(stream_broker.queue_name, "taskiq"))["pending"] == 0


async def test_listen_declares_the_group_again_after_a_flush(
    stream_broker: AckedStreamBroker,
) -> None:
    async with Redis(connection_pool=stream_broker.connection_pool) as redis:
        await redis.delete(stream_broker.queue_name)
    listen = stream_broker.listen()
    first = asyncio.ensure_future(anext(listen))
    await asyncio.sleep(0.3)  # one read refused with NOGROUP, the group declared again
    await stream_broker.find_task("ping").kiq()  # type: ignore[union-attr]

    message = await asyncio.wait_for(first, timeout=5)
    await listen.aclose()

    assert stream_broker.formatter.loads(message.data).task_name == "ping"
