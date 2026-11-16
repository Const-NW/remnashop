from typing import Final, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1012"
down_revision: Union[str, None] = "1011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# every user.expiration webhook so far was stored under the opposite type
# (the panel's sign was read backwards); hours were already positive
_SWAP: Final[str] = """
    UPDATE {table} SET event_type = CASE event_type
        WHEN 'SubscriptionExpiringEvent' THEN 'SubscriptionExpiredAgoEvent'
        ELSE 'SubscriptionExpiringEvent'
    END
    WHERE event_type IN ('SubscriptionExpiringEvent', 'SubscriptionExpiredAgoEvent')
"""
_TABLES: Final[tuple[str, ...]] = ("event_log_entries", "event_outbox")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(sa.text(_SWAP.format(table=table)))


def downgrade() -> None:
    upgrade()
