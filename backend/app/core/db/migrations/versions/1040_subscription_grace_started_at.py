from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1040"
down_revision: Union[str, None] = "1039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions", sa.Column("grace_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    # a grace already running must not be entered, and announced, again
    op.execute("UPDATE subscriptions SET grace_started_at = now() WHERE grace_until IS NOT NULL")


def downgrade() -> None:
    op.drop_column("subscriptions", "grace_started_at")
