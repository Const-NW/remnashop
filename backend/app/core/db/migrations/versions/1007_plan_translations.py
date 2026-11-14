from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1007"
down_revision: Union[str, None] = "1006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "plan_translations",
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column(
            "locale",
            postgresql.ENUM(name="locale", create_type=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            name=op.f("fk_plan_translations_plan_id_plans"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("plan_id", "locale", name=op.f("pk_plan_translations")),
    )


def downgrade() -> None:
    op.drop_table("plan_translations")
