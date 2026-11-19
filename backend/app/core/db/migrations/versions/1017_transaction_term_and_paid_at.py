from typing import Final, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1017"
down_revision: Union[str, None] = "1016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PAID_NOT_TEST: Final[str] = "status = 'PAID' AND NOT is_test"


def upgrade() -> None:
    op.add_column("transactions", sa.Column("added_days", sa.Integer(), nullable=True))
    op.add_column(
        "subscription_purchase_idempotency",
        sa.Column("renew_added_days", sa.Integer(), nullable=True),
    )
    op.add_column("transactions", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    # a PAID order's updated_at was kept at its paid moment; a refund moved it, so a
    # refunded order takes its delivery (right after payment) or its checkout instead
    op.execute("UPDATE transactions SET paid_at = updated_at WHERE status = 'PAID'")
    op.execute(
        "UPDATE transactions SET paid_at = COALESCE(fulfilled_at, created_at) "
        "WHERE status = 'REFUNDED'"
    )
    op.create_check_constraint(
        op.f("ck_transactions_paid_has_paid_at"),
        "transactions",
        "status <> 'PAID' OR paid_at IS NOT NULL",
    )
    op.drop_index("ix_transactions_paid_updated_at", table_name="transactions")
    op.create_index(
        "ix_transactions_paid_at",
        "transactions",
        ["paid_at"],
        postgresql_where=sa.text(_PAID_NOT_TEST),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_paid_at", table_name="transactions")
    op.create_index(
        "ix_transactions_paid_updated_at",
        "transactions",
        ["updated_at"],
        postgresql_where=sa.text(_PAID_NOT_TEST),
    )
    op.drop_constraint(op.f("ck_transactions_paid_has_paid_at"), "transactions", type_="check")
    # the old code dates revenue by a PAID order's updated_at, which this one lets move
    op.execute("UPDATE transactions SET updated_at = paid_at WHERE status = 'PAID'")
    op.drop_column("transactions", "paid_at")
    op.drop_column("subscription_purchase_idempotency", "renew_added_days")
    op.drop_column("transactions", "added_days")
