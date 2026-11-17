from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1014"
down_revision: Union[str, None] = "1013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_gateways",
        sa.Column("telegram_enabled", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "payment_gateways",
        sa.Column("cabinet_enabled", sa.Boolean(), server_default="true", nullable=False),
    )
    # digital goods in the bot and Mini Apps are sold for Stars only
    op.execute("UPDATE payment_gateways SET telegram_enabled = true WHERE type = 'TELEGRAM_STARS'")
    op.add_column(
        "user_sessions",
        sa.Column("telegram_mini_app", sa.Boolean(), server_default="false", nullable=False),
    )
    # a free order is recorded without a gateway
    op.alter_column("transactions", "gateway_type", nullable=True)


def downgrade() -> None:
    # the old schema has no gateway-less order: free ones take Stars, which never charged them
    op.execute("UPDATE transactions SET gateway_type = 'TELEGRAM_STARS' WHERE gateway_type IS NULL")
    op.alter_column("transactions", "gateway_type", nullable=False)
    op.drop_column("user_sessions", "telegram_mini_app")
    op.drop_column("payment_gateways", "cabinet_enabled")
    op.drop_column("payment_gateways", "telegram_enabled")
