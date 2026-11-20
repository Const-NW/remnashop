from typing import Sequence, Union

from alembic import op

revision: str = "1026"
down_revision: Union[str, None] = "1025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_ad_links_some_channel"), "ad_links", "allow_bot OR allow_webcabinet"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_ad_links_some_channel"), "ad_links", type_="check")
