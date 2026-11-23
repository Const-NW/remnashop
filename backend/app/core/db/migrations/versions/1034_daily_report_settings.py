from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1034"
down_revision: Union[str, None] = "1033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_settings",
        sa.Column("report_timezone", sa.String(length=64), server_default="UTC", nullable=False),
    )
    op.add_column(
        "notification_settings",
        sa.Column("daily_report_hour", sa.SmallInteger(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("notification_settings", "daily_report_hour")
    op.drop_column("notification_settings", "report_timezone")
