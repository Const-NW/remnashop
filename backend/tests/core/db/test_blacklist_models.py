import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Blacklist, BlacklistSource


async def test_blacklist_singleton_defaults(session: AsyncSession) -> None:
    session.add(Blacklist(id=1))
    await session.flush()
    row = (await session.execute(select(Blacklist))).scalar_one()
    assert row.blocked_telegram_ids == []
    assert row.blocked_emails == []


async def test_blacklist_source_url_unique(session: AsyncSession) -> None:
    session.add(BlacklistSource(url="https://a.example/list.txt"))
    await session.flush()
    session.add(BlacklistSource(url="https://a.example/list.txt"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_sources_lists_load_only_when_asked_for(session: AsyncSession) -> None:
    session.add(BlacklistSource(url="https://lists.test/deferred", blocked_telegram_ids=[1]))
    await session.flush()
    session.expunge_all()

    row = await session.scalar(
        select(BlacklistSource).where(BlacklistSource.url == "https://lists.test/deferred")
    )

    assert row is not None
    assert {"blocked_telegram_ids", "blocked_emails"} <= inspect(row).unloaded
