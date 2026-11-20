import asyncio
from typing import Final

from loguru import logger
from prometheus_client import Gauge
from redis.asyncio import Redis

from app.core.metrics import WORKER_METRICS_REGISTRY

QUEUE_DEPTH: Final[Gauge] = Gauge(
    "remnashop_taskiq_queue_depth",
    "Taskiq messages not yet acked: unread plus in progress",
    registry=WORKER_METRICS_REGISTRY,
)

STREAM_LENGTH: Final[Gauge] = Gauge(
    "remnashop_taskiq_stream_length",
    "Entries in the taskiq stream, acked ones not yet trimmed included",
    registry=WORKER_METRICS_REGISTRY,
)

SCHEDULED_RETRIES: Final[Gauge] = Gauge(
    "remnashop_taskiq_scheduled_retries",
    "Pending scheduled taskiq retries",
    registry=WORKER_METRICS_REGISTRY,
)

_WORKER_GAUGE_POLL_INTERVAL_S: Final[float] = 15.0


async def _sample_queue_depth(redis: Redis, queue_name: str, group_name: str) -> None:
    groups = await redis.xinfo_groups(queue_name)
    group = next((g for g in groups if g["name"] in (group_name, group_name.encode())), None)
    if group is None:  # flushed, the stream since recreated by a kick
        return
    # lag is unknown past a deleted entry; trimming only drops acked ones
    QUEUE_DEPTH.set((group["lag"] or 0) + group["pending"])
    # far above the depth: the trim is stuck behind an old pending message
    STREAM_LENGTH.set(await redis.xlen(queue_name))


async def _sample_scheduled_retries(redis: Redis, schedule_prefix: str) -> None:
    count = 0
    async for _ in redis.scan_iter(f"{schedule_prefix}:data:*", count=1000):
        count += 1
    SCHEDULED_RETRIES.set(count)


async def poll_worker_gauges(
    redis: Redis, queue_name: str, group_name: str, retry_schedule_prefix: str
) -> None:
    while True:
        try:
            await _sample_queue_depth(redis, queue_name, group_name)
            await _sample_scheduled_retries(redis, retry_schedule_prefix)
        except Exception:  # a transient Redis blip must not kill the poller
            logger.warning("Failed to sample taskiq worker gauges")
        await asyncio.sleep(_WORKER_GAUGE_POLL_INTERVAL_S)
