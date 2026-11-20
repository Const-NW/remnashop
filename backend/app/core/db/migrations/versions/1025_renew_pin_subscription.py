from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1025"
down_revision: Union[str, None] = "1024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription_purchase_idempotency",
        sa.Column("renew_subscription_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription_purchase_idempotency", "renew_subscription_id")
