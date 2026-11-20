import asyncio
import time
import weakref
from collections.abc import AsyncGenerator
from contextlib import suppress
from functools import partial
from typing import Any, Final, Optional, Union

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import ResponseError
from taskiq import AckableMessage
from taskiq_redis import RedisStreamBroker

# the list ListQueueBroker pushed to; a worker moves what it holds into the stream
# on start and every heartbeat, as processes from before the upgrade may still push
LEGACY_LIST_QUEUE: Final[str] = "taskiq"
# a message no live consumer has touched this long goes to another one
REDELIVER_AFTER_MS: Final[int] = 5 * 60 * 1000
# under REDELIVER_AFTER_MS, so a task running for hours is never handed out twice
_HEARTBEAT_S: Final[float] = 60.0
# handed out this often without an ack: a task that takes its process down, or one
# the receiver drops, is given up instead of going round forever
MAX_DELIVERIES: Final[int] = 5
# held this long, a task is taken as hung and its message given up: a rerun would
# likely hang too, and the trim would stay stuck behind it
MAX_HOLD_S: Final[float] = 6 * 60 * 60
# ListQueueBroker pushed left and popped right: the oldest is on the right. One
# script, so a kill between the pop and the add can't lose the message
_MOVE_OLDEST: Final[str] = """
local data = redis.call('RPOP', KEYS[1])
if not data then return 0 end
redis.call('XADD', KEYS[2], '*', 'data', data)
return 1
"""


class AckedStreamBroker(RedisStreamBroker):
    """A Redis stream broker whose worker keeps the messages it holds: a crashed
    process's messages go to another one after REDELIVER_AFTER_MS, while a live one
    renews its claim on every message it took, however long the task runs."""

    def __init__(self, url: str, *, queue_name: str, **kwargs: Any) -> None:
        super().__init__(
            url,
            queue_name=queue_name,
            # from the stream's start: a message queued before any worker ran counts
            consumer_id="0",
            idle_timeout=REDELIVER_AFTER_MS,
            **kwargs,
        )
        # read and not yet acked, with when it was first held: renewed until the ack lands
        self._held: dict[bytes, float] = {}
        # acked by the task, refused by Redis: retried with the renewal
        self._ack_failed: set[bytes] = set()
        self._maintenance: Optional[asyncio.Task[None]] = None
        # XAUTOCLAIM scans a bounded slice of the pending list per call: carried on
        self._reclaim_from: Union[str, bytes] = "0-0"

    async def startup(self) -> None:
        await super().startup()
        if self.is_worker_process:
            await self._drain_legacy_list()
            self._maintenance = asyncio.create_task(self._maintain())

    async def shutdown(self) -> None:
        if self._maintenance is not None:
            self._maintenance.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance
        await super().shutdown()

    async def listen(self) -> AsyncGenerator[AckableMessage, None]:
        """New messages, then any a dead consumer left past REDELIVER_AFTER_MS. Each
        batch is held as soon as it's read, so a long wait for a free slot doesn't
        hand its tail to another consumer."""
        async with Redis(connection_pool=self.connection_pool) as redis:
            while True:
                try:
                    batch = await self._read_batch(redis)
                except ResponseError as error:
                    if not _no_group(error):
                        raise
                    # the database was flushed: start over instead of taking the process down
                    logger.warning("Task stream group is gone, declaring it again")
                    self._held.clear()
                    self._ack_failed.clear()
                    self._reclaim_from = "0-0"
                    await self._declare_consumer_group()
                    # declaring can fail too (Redis out of memory): no busy loop
                    await asyncio.sleep(1)
                    continue
                now = time.monotonic()
                self._held.update((message_id, now) for message_id, _ in batch)
                for message_id, fields in batch:
                    ack = partial(self._ack, message_id)
                    if self._runnable(fields[b"data"]):
                        message = AckableMessage(data=fields[b"data"], ack=ack)
                        # dropped unacked (a middleware raised past the ack): no longer
                        # renewed, so it goes out again after REDELIVER_AFTER_MS
                        weakref.finalize(message, self._release, message_id)
                        # the hang ceiling counts from the run, not from a wait for a slot
                        self._held[message_id] = time.monotonic()
                        yield message
                        del message  # only the receiver keeps it alive
                    else:
                        # redelivered forever otherwise, and the stream never trimmed past it
                        await ack()

    async def _read_batch(self, redis: Redis) -> list[tuple[bytes, dict[bytes, bytes]]]:
        fetched = await redis.xreadgroup(
            self.consumer_group_name,
            self.consumer_name,
            {self.queue_name: ">"},
            block=self.block,
            count=self.count,
        )
        batch = [entry for _, entries in fetched for entry in entries]
        # atomic, and it resets what it claims, so no lock against other consumers
        self._reclaim_from, claimed, *_ = await redis.xautoclaim(
            self.queue_name,
            self.consumer_group_name,
            self.consumer_name,
            min_idle_time=self.idle_timeout,
            count=self.unacknowledged_batch_size,
            start_id=self._reclaim_from,
        )
        for entry in claimed:
            # our own after a long stall of this loop: already running or queued here
            if entry[0] in self._held:
                continue
            if await self._delivered_too_often(redis, entry[0]):
                await self._ack(entry[0])
            else:
                batch.append(entry)
        return batch

    async def _delivered_too_often(self, redis: Redis, message_id: bytes) -> bool:
        detail = await redis.xpending_range(
            self.queue_name, self.consumer_group_name, min=message_id, max=message_id, count=1
        )
        if not detail or detail[0]["times_delivered"] <= MAX_DELIVERIES:
            return False
        logger.error(
            f"Gave up task message '{message_id!r}' after {MAX_DELIVERIES} deliveries "
            "without an ack"
        )
        return True

    def _release(self, message_id: bytes) -> None:
        # an ack Redis refused still holds it until the retry lands
        if message_id not in self._ack_failed:
            self._held.pop(message_id, None)

    async def _ack(self, message_id: bytes) -> None:
        try:
            async with Redis(connection_pool=self.connection_pool) as redis:
                await redis.xack(self.queue_name, self.consumer_group_name, message_id)
        except Exception:
            logger.exception(f"Failed to ack task message '{message_id!r}', retrying")
            self._ack_failed.add(message_id)
            return
        self._held.pop(message_id, None)

    def _runnable(self, data: bytes) -> bool:
        try:
            name = self.formatter.loads(data).task_name
        except Exception:
            logger.warning(f"Dropped an unreadable task message: {data[:200]!r}")
            return False
        if self.find_task(name) is None:
            logger.warning(f"Dropped a message for unknown task '{name}'")
            return False
        return True

    async def _drain_legacy_list(self) -> None:
        moved = 0
        async with Redis(connection_pool=self.connection_pool) as redis:
            if await redis.type(LEGACY_LIST_QUEUE) not in (b"list", "list"):
                return
            move_oldest = redis.register_script(_MOVE_OLDEST)
            while await move_oldest(keys=[LEGACY_LIST_QUEUE, self.queue_name]):
                moved += 1
        if moved:
            logger.info(f"Moved {moved} queued task(s) from the old list queue to the stream")

    async def _maintain(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_S)
            try:
                await self._drain_legacy_list()
                await self._retry_failed_acks()
                await self._renew_held()
                await self._trim_acked()
            except Exception as error:
                if isinstance(error, ResponseError) and _no_group(error):
                    # flushed: listen declares the group again on its next read
                    logger.warning("Task stream group is gone, maintenance skipped")
                else:
                    logger.exception("Task stream maintenance failed")

    async def _retry_failed_acks(self) -> None:
        for message_id in list(self._ack_failed):
            self._ack_failed.discard(message_id)
            await self._ack(message_id)

    async def _renew_held(self) -> None:
        cutoff = time.monotonic() - MAX_HOLD_S
        for message_id, since in list(self._held.items()):
            if since < cutoff and message_id not in self._ack_failed:
                logger.error(
                    f"Task message '{message_id!r}' held past {MAX_HOLD_S / 3600:.0f}h, "
                    "taken as hung: given up"
                )
                await self._ack(message_id)
        if not self._held:
            return
        async with Redis(connection_pool=self.connection_pool) as redis:
            await redis.xclaim(
                self.queue_name,
                self.consumer_group_name,
                self.consumer_name,
                min_idle_time=0,
                message_ids=list(self._held),
                justid=True,
            )

    async def _trim_acked(self) -> None:
        """Drops what every consumer has acked: older than the oldest pending
        message, or than the last one handed out when none is pending."""
        async with Redis(connection_pool=self.connection_pool) as redis:
            # read first: a message handed out after it is newer, so never trimmed
            groups = await redis.xinfo_groups(self.queue_name)
            group = next((g for g in groups if _text(g["name"]) == self.consumer_group_name), None)
            if group is None:  # flushed, the stream since recreated by a kick
                return
            pending = await redis.xpending(self.queue_name, self.consumer_group_name)
            oldest = pending["min"] if pending["pending"] else group["last-delivered-id"]
            if _text(oldest) != "0-0":
                await redis.xtrim(self.queue_name, minid=oldest, approximate=False)


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _no_group(error: ResponseError) -> bool:
    return str(error).startswith("NOGROUP")
