from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1009"
down_revision: Union[str, None] = "1008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shop_settings",
        sa.Column(
            "plan_change_credit_enabled", sa.Boolean(), server_default="true", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("shop_settings", "plan_change_credit_enabled")
