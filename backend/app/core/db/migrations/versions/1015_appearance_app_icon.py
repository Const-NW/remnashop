from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1015"
down_revision: Union[str, None] = "1014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("appearance_settings", sa.Column("app_icon_content_type", sa.String(length=32)))
    op.add_column("appearance_settings", sa.Column("app_icon_extension", sa.String(length=8)))


def downgrade() -> None:
    op.drop_column("appearance_settings", "app_icon_extension")
    op.drop_column("appearance_settings", "app_icon_content_type")
