from collections.abc import Collection
from typing import Final, Optional, Union

from sqlalchemy import ARRAY, BigInteger, ColumnElement, any_, literal, or_, select
from sqlalchemy.orm import InstrumentedAttribute

from app.core.db.models.user import User
from app.core.enums import Role

_IntColumn = Union[
    InstrumentedAttribute[int], InstrumentedAttribute[Optional[int]], ColumnElement[int]
]

_STAFF_ROLES: Final[tuple[Role, ...]] = tuple(role for role in Role if role is not Role.USER)


def in_ids(column: _IntColumn, ids: Collection[int]) -> ColumnElement[bool]:
    # one array bind, not IN (...): a long id list would pass asyncpg's
    # 32,767 bind-parameter cap
    return column == any_(literal(list(ids), ARRAY(BigInteger)))


def customer_rows(column: _IntColumn) -> ColumnElement[bool]:
    """Rows of a NOT NULL user id column that belong to customers. Excludes the
    few staff ids instead of matching every customer: past a few hundred thousand
    customers an `IN (customers)` under an OR stops fitting a hash and goes quadratic."""
    # `in_`, not `!= USER`: an inequality can't use ix_users_role
    return column.notin_(select(User.id).where(User.role.in_(_STAFF_ROLES)))


def visible_rows(column: _IntColumn, viewer_id: int) -> ColumnElement[bool]:
    # same rule as visible_user: staff rows are hidden, the viewer's own are not
    return or_(customer_rows(column), column == viewer_id)
