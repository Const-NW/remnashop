from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.filters import customer_rows, visible_rows
from app.core.db.models.user import User
from app.core.enums import Locale, Role


async def test_staff_rows_are_hidden_except_the_viewers_own(session: AsyncSession) -> None:
    customer = User(name="customer", role=Role.USER, language=Locale.EN, is_blocked=False)
    viewer = User(name="viewer", role=Role.ADMIN, language=Locale.EN, is_blocked=False)
    other = User(name="other-admin", role=Role.ADMIN, language=Locale.EN, is_blocked=False)
    session.add_all([customer, viewer, other])
    await session.flush()
    ours = [customer.id, viewer.id, other.id]

    async def matching(condition: ColumnElement[bool]) -> set[int]:
        return set(await session.scalars(select(User.id).where(condition, User.id.in_(ours))))

    assert await matching(customer_rows(User.id)) == {customer.id}
    assert await matching(visible_rows(User.id, viewer.id)) == {customer.id, viewer.id}
