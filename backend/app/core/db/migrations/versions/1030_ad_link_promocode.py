from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1030"
down_revision: Union[str, None] = "1029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ad_links", sa.Column("promocode_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_ad_links_promocode_id_promocodes"),
        "ad_links",
        "promocodes",
        ["promocode_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_ad_links_promocode_id_promocodes"), "ad_links", type_="foreignkey")
    op.drop_column("ad_links", "promocode_id")
