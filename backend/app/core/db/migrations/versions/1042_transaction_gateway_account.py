from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1042"
down_revision: Union[str, None] = "1041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("gateway_account", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "gateway_account")
