from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1024"
down_revision: Union[str, None] = "1023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "promocodes",
        sa.Column("prior_customers_only", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("promocodes", "prior_customers_only")
