from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1038"
down_revision: Union[str, None] = "1037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_subscriptions_grace_until",
        "subscriptions",
        ["grace_until"],
        unique=False,
        postgresql_where=sa.text("grace_until IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_grace_until", table_name="subscriptions")
