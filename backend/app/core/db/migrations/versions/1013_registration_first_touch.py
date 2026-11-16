from typing import Final, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1013"
down_revision: Union[str, None] = "1012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS: Final[tuple[tuple[str, int], ...]] = (
    ("landing_url", 512),
    ("referer", 512),
    ("utm_source", 128),
    ("utm_medium", 128),
    ("utm_campaign", 128),
    ("utm_content", 128),
    ("utm_term", 128),
    ("click_id", 256),
    ("platform", 32),
)


def upgrade() -> None:
    for name, length in _COLUMNS:
        op.add_column("user_registrations", sa.Column(name, sa.String(length), nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("user_registrations", name)
