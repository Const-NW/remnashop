from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1018"
down_revision: Union[str, None] = "1017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # filled by webhooks and syncs; until then the bot asks the panel
    op.add_column(
        "subscriptions", sa.Column("panel_created_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "panel_created_at")
