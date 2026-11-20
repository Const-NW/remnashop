from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1020"
down_revision: Union[str, None] = "1019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HOLD = postgresql.ENUM("PURCHASE_DISCOUNT_SPENT", "PLAN_CHANGED", name="fulfillmenthold")


def upgrade() -> None:
    _HOLD.create(op.get_bind())
    op.add_column(
        "transactions",
        sa.Column("fulfillment_hold", postgresql.ENUM(name="fulfillmenthold", create_type=False)),
    )


def downgrade() -> None:
    op.drop_column("transactions", "fulfillment_hold")
    _HOLD.drop(op.get_bind())
