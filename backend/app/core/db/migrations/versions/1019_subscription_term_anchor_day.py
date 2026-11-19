from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1019"
down_revision: Union[str, None] = "1018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # no backfill: a row without an anchor counts its next term from its expiry's day
    op.add_column("subscriptions", sa.Column("term_anchor_day", sa.SmallInteger(), nullable=True))
    op.add_column(
        "subscription_purchase_idempotency",
        sa.Column("renew_anchor_day", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription_purchase_idempotency", "renew_anchor_day")
    op.drop_column("subscriptions", "term_anchor_day")
