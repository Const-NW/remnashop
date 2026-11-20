from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1023"
down_revision: Union[str, None] = "1022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HOLD = postgresql.ENUM("PURCHASE_DISCOUNT_SPENT", "PLAN_CHANGED", name="fulfillmenthold")


def upgrade() -> None:
    _HOLD.create(op.get_bind())
    op.add_column(
        "transactions",
        sa.Column("fulfillment_hold", postgresql.ENUM(name="fulfillmenthold", create_type=False)),
    )
    for column in ("fulfillment_approved", "purchase_discount_returned"):
        op.add_column(
            "transactions",
            sa.Column(column, sa.Boolean(), server_default=sa.false(), nullable=False),
        )
    # canceled before this column, their discount was handed back as now; a late payment
    # spends it again, or holds the order when it's gone
    op.execute(
        "UPDATE transactions SET purchase_discount_returned = true "
        "WHERE status IN ('CANCELED', 'FAILED') AND discount_source = 'PURCHASE'"
    )


def downgrade() -> None:
    op.drop_column("transactions", "purchase_discount_returned")
    op.drop_column("transactions", "fulfillment_approved")
    op.drop_column("transactions", "fulfillment_hold")
    _HOLD.drop(op.get_bind())
