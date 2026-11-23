from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1039"
down_revision: Union[str, None] = "1038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions", sa.Column("grace_attempted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "grace_attempted_at")
