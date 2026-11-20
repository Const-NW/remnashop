from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1022"
down_revision: Union[str, None] = "1021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # empty: the next push sends the menu and records it
    op.add_column("bot_settings", sa.Column("commands_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_settings", "commands_hash")
