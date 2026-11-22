from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1031"
down_revision: Union[str, None] = "1030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscription_device_names",
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("hwid", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name=op.f("fk_subscription_device_names_subscription_id_subscriptions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "subscription_id", "hwid", name=op.f("pk_subscription_device_names")
        ),
    )


def downgrade() -> None:
    op.drop_table("subscription_device_names")
