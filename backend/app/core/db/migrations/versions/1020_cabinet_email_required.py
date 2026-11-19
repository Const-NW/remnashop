from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1020"
down_revision: Union[str, None] = "1019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cabinet_auth_settings",
        sa.Column("email_required", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("cabinet_auth_settings", "email_required")
