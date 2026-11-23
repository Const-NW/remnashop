from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1035"
down_revision: Union[str, None] = "1034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "grace_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("internal_squads", sa.ARRAY(sa.UUID()), server_default="{}", nullable=False),
        sa.Column("external_squad", sa.UUID(), nullable=True),
        sa.Column("traffic_limit_mb", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "traffic_limit_strategy",
            postgresql.ENUM(
                "NO_RESET",
                "DAY",
                "WEEK",
                "MONTH",
                "MONTH_ROLLING",
                name="trafficlimitstrategy",
                create_type=False,
            ),
            server_default="NO_RESET",
            nullable=False,
        ),
        sa.Column("tag", sa.String(), nullable=True),
        sa.Column("duration_days", sa.Integer(), server_default="3", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grace_settings")),
    )


def downgrade() -> None:
    op.drop_table("grace_settings")
    # ### end Alembic commands ###
