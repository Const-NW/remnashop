from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1029"
down_revision: Union[str, None] = "1028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("allow_renewal", sa.Boolean(), server_default="true", nullable=False),
    )
    # a switched-off plan stopped its renewals until now: it keeps doing so
    op.execute("UPDATE plans SET allow_renewal = is_active")


def downgrade() -> None:
    op.drop_column("plans", "allow_renewal")
