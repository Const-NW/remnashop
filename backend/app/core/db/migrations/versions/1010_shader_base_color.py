from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1010"
down_revision: Union[str, None] = "1009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The cosine palette's terms don't translate into colors one to one: a saved
# palette is dropped and the background goes back to following the accent.
def upgrade() -> None:
    op.add_column(
        "appearance_settings", sa.Column("shader_base", sa.String(length=7), nullable=True)
    )
    op.add_column(
        "appearance_settings",
        sa.Column("shader_tones", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_column("appearance_settings", "shader_palette")


def downgrade() -> None:
    op.add_column(
        "appearance_settings",
        sa.Column("shader_palette", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.drop_column("appearance_settings", "shader_tones")
    op.drop_column("appearance_settings", "shader_base")
