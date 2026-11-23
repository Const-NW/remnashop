from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1041"
down_revision: Union[str, None] = "1040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("panel_restore_due_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_subscriptions_panel_restore_due_at",
        "subscriptions",
        ["panel_restore_due_at"],
        postgresql_where=sa.text("panel_restore_due_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_panel_restore_due_at", table_name="subscriptions")
    op.drop_column("subscriptions", "panel_restore_due_at")
