from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1032"
down_revision: Union[str, None] = "1031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("markup_percent", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_users_markup_percent_range"), "users", "markup_percent BETWEEN 0 AND 500"
    )
    op.add_column(
        "transactions",
        sa.Column("markup_percent", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("transactions", "markup_percent")
    op.drop_constraint(op.f("ck_users_markup_percent_range"), "users", type_="check")
    op.drop_column("users", "markup_percent")
