from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1016"
down_revision: Union[str, None] = "1015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Telegram Stars charge ids run past 128 characters, with no documented cap
    op.alter_column(
        "transactions",
        "external_payment_id",
        type_=sa.String(),
        existing_type=sa.String(length=128),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "transactions",
        "external_payment_id",
        type_=sa.String(length=128),
        existing_type=sa.String(),
        existing_nullable=True,
        postgresql_using="left(external_payment_id, 128)",
    )
