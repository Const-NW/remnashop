from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1033"
down_revision: Union[str, None] = "1032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_settings",
        sa.Column("remove_reply_keyboard", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("bot_settings", "remove_reply_keyboard")
