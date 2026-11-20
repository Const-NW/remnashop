from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1021"
down_revision: Union[str, None] = "1020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # filled by the telegram process on its next start
    op.add_column("bot_settings", sa.Column("username", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_settings", "username")
