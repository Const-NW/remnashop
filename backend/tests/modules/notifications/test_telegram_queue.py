from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis

from app.modules.notifications import telegram_queue
from app.modules.notifications.telegram_queue import AdminTelegramQueue
from app.modules.notifications.usecases import DrainAdminTelegramUseCase
from tests.fakes.telegram_bot import FakeBotSession, flood, refusal, use_fake_bot

_TOKEN = "123:abc"


@pytest.fixture
async def queue(redis: Redis) -> AsyncIterator[AdminTelegramQueue]:
    keys = (telegram_queue._HOLD, telegram_queue._QUEUE)
    await redis.delete(*keys)
    yield AdminTelegramQueue(redis)
    await redis.delete(*keys)


def _replies(*replies: object) -> FakeBotSession:
    pending = list(replies)

    def _send(params: dict[str, Any]) -> object:
        return pending.pop(0) if pending else {"message_id": 1}

    return FakeBotSession({"sendMessage": _send})


def _sent(session: FakeBotSession) -> list[tuple[int, str]]:
    return [(p["chat_id"], p["text"]) for p in session.called("sendMessage")]


async def test_a_card_goes_straight_out_without_a_flood(
    monkeypatch: pytest.MonkeyPatch, queue: AdminTelegramQueue, redis: Redis
) -> None:
    session = use_fake_bot(monkeypatch, _replies())

    await queue.send(_TOKEN, None, 1, "a")

    assert _sent(session) == [(1, "a")]
    assert session.called("sendMessage")[0]["parse_mode"] == "HTML"
    assert await redis.llen(telegram_queue._QUEUE) == 0  # type: ignore[misc]


async def test_a_flooded_card_and_the_ones_after_it_wait_in_order(
    monkeypatch: pytest.MonkeyPatch, queue: AdminTelegramQueue, redis: Redis
) -> None:
    session = use_fake_bot(monkeypatch, _replies(flood(120)))

    await queue.send(_TOKEN, None, 1, "a")
    await queue.send(_TOKEN, None, 2, "b")

    # the second card never reached Telegram while the hold lasts
    assert _sent(session) == [(1, "a")]
    assert 0 < await redis.pttl(telegram_queue._HOLD) <= 120_000
    assert await queue.drain(_TOKEN, None) == 0

    await redis.delete(telegram_queue._HOLD)
    # the hold is gone but cards are still queued: a new one goes behind them
    await queue.send(_TOKEN, None, 3, "c")
    assert await queue.drain(_TOKEN, None) == 3

    assert _sent(session) == [(1, "a"), (1, "a"), (2, "b"), (3, "c")]
    assert await redis.llen(telegram_queue._QUEUE) == 0  # type: ignore[misc]


async def test_a_flood_during_the_drain_keeps_the_card_first(
    monkeypatch: pytest.MonkeyPatch, queue: AdminTelegramQueue, redis: Redis
) -> None:
    session = use_fake_bot(monkeypatch, _replies(flood(5), None, flood(5)))
    await queue.send(_TOKEN, None, 1, "a")
    await queue.send(_TOKEN, None, 2, "b")
    await redis.delete(telegram_queue._HOLD)

    assert await queue.drain(_TOKEN, None) == 1

    assert _sent(session) == [(1, "a"), (1, "a"), (2, "b")]
    assert await redis.exists(telegram_queue._HOLD)
    await redis.delete(telegram_queue._HOLD)
    assert await queue.drain(_TOKEN, None) == 1
    assert _sent(session)[-1] == (2, "b")


async def test_a_refused_card_is_dropped_not_retried(
    monkeypatch: pytest.MonkeyPatch, queue: AdminTelegramQueue, redis: Redis
) -> None:
    session = use_fake_bot(monkeypatch, _replies(flood(5), refusal(403, "Forbidden")))
    await queue.send(_TOKEN, None, 1, "a")
    await queue.send(_TOKEN, None, 2, "b")
    await redis.delete(telegram_queue._HOLD)

    assert await queue.drain(_TOKEN, None) == 2

    assert _sent(session) == [(1, "a"), (1, "a"), (2, "b")]
    assert await redis.llen(telegram_queue._QUEUE) == 0  # type: ignore[misc]


async def test_the_queue_keeps_only_the_newest_cards(
    monkeypatch: pytest.MonkeyPatch, queue: AdminTelegramQueue, redis: Redis
) -> None:
    monkeypatch.setattr(telegram_queue, "_MAX_QUEUED", 2)
    use_fake_bot(monkeypatch, _replies(flood(60)))

    for chat_id in (1, 2, 3):
        await queue.send(_TOKEN, None, chat_id, "x")

    queued = await redis.lrange(telegram_queue._QUEUE, 0, -1)  # type: ignore[misc]
    assert ['"chat_id":2' in queued[0], '"chat_id":3' in queued[1]] == [True, True]


async def test_a_card_moved_under_the_drain_is_not_popped(
    queue: AdminTelegramQueue, redis: Redis
) -> None:
    await redis.rpush(telegram_queue._QUEUE, "first", "second")  # type: ignore[misc]

    # another drain or a trim already took "first": popping blindly would lose "second"
    await redis.lpop(telegram_queue._QUEUE)  # type: ignore[misc]
    await queue._pop_if_head("first")

    assert await redis.lrange(telegram_queue._QUEUE, 0, -1) == ["second"]  # type: ignore[misc]


async def test_an_unreadable_or_unreachable_card_leaves_the_queue(
    monkeypatch: pytest.MonkeyPatch, queue: AdminTelegramQueue, redis: Redis
) -> None:
    session = use_fake_bot(monkeypatch, _replies(flood(5), TimeoutError()))
    await queue.send(_TOKEN, None, 1, "a")
    await redis.rpush(telegram_queue._QUEUE, "not json")  # type: ignore[misc]
    await redis.delete(telegram_queue._HOLD)

    assert await queue.drain(_TOKEN, None) == 1

    assert _sent(session) == [(1, "a"), (1, "a")]
    assert await redis.llen(telegram_queue._QUEUE) == 0  # type: ignore[misc]


class _NoToken:
    async def get_bot_token(self) -> tuple[None, None]:
        return None, None


async def test_without_a_token_the_queue_is_kept(
    monkeypatch: pytest.MonkeyPatch, queue: AdminTelegramQueue, redis: Redis
) -> None:
    use_fake_bot(monkeypatch, _replies(flood(5)))
    await queue.send(_TOKEN, None, 1, "a")
    await redis.delete(telegram_queue._HOLD)

    drain = DrainAdminTelegramUseCase(_NoToken(), queue)  # type: ignore[arg-type]

    assert await drain.system() == 0
    assert await redis.llen(telegram_queue._QUEUE) == 1  # type: ignore[misc]
