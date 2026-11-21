from typing import Final, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1028"
down_revision: Union[str, None] = "1027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TRGM_TO_PUBLIC: Final[str] = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
        WHERE e.extname = 'pg_trgm' AND n.nspname = 'legacy'
    ) THEN
        ALTER EXTENSION pg_trgm SET SCHEMA public;
    END IF;
END $$
"""


def upgrade() -> None:
    # trusted extension (PG 13+): needs CREATE on the database, not superuser
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # a migrated old database may have had it already: renaming its `public` to
    # `legacy` took the extension along, out of reach of an unqualified gin_trgm_ops
    op.execute(_TRGM_TO_PUBLIC)
    op.drop_index(op.f("ix_broadcast_recipients_status"), table_name="broadcast_recipients")
    op.create_index(
        "ix_broadcast_recipients_status_channel_id",
        "broadcast_recipients",
        ["broadcast_id", "status", "channel", "id"],
        unique=False,
    )
    op.drop_index(op.f("ix_customer_notifications_user_id"), table_name="customer_notifications")
    op.drop_index(op.f("ix_event_outbox_category"), table_name="event_outbox")
    op.drop_index(op.f("ix_event_outbox_event_type"), table_name="event_outbox")
    op.drop_index(op.f("ix_event_outbox_processed_at"), table_name="event_outbox")
    op.create_index(
        "ix_event_outbox_unprocessed",
        "event_outbox",
        ["id"],
        unique=False,
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.create_index(
        "ix_transactions_anchor_unrated",
        "transactions",
        ["currency"],
        unique=False,
        postgresql_where=sa.text("anchor_unrated IS TRUE"),
    )
    op.create_index(
        "ix_transactions_customer_email_trgm",
        "transactions",
        ["customer_email"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"customer_email": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_transactions_external_payment_id_trgm",
        "transactions",
        ["external_payment_id"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"external_payment_id": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_transactions_status_updated_at",
        "transactions",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "ix_transactions_plan_name_trgm",
        "transactions",
        [sa.literal_column("(plan_snapshot ->> 'name') gin_trgm_ops")],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "ix_users_email_trgm",
        "users",
        ["email"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"email": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_users_email_undeliverable_at",
        "users",
        ["email_undeliverable_at"],
        unique=False,
        postgresql_where=sa.text("email_undeliverable_at IS NOT NULL"),
    )
    op.create_index(
        "ix_users_login_trgm",
        "users",
        ["login"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"login": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_users_name_trgm",
        "users",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_users_username_trgm",
        "users",
        ["username"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"username": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_users_username_trgm",
        table_name="users",
        postgresql_using="gin",
        postgresql_ops={"username": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_users_name_trgm",
        table_name="users",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_users_login_trgm",
        table_name="users",
        postgresql_using="gin",
        postgresql_ops={"login": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_users_email_undeliverable_at",
        table_name="users",
        postgresql_where=sa.text("email_undeliverable_at IS NOT NULL"),
    )
    op.drop_index(
        "ix_users_email_trgm",
        table_name="users",
        postgresql_using="gin",
        postgresql_ops={"email": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_transactions_plan_name_trgm", table_name="transactions", postgresql_using="gin"
    )
    op.drop_index(
        "ix_transactions_status_updated_at",
        table_name="transactions",
    )
    op.drop_index(
        "ix_transactions_external_payment_id_trgm",
        table_name="transactions",
        postgresql_using="gin",
        postgresql_ops={"external_payment_id": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_transactions_customer_email_trgm",
        table_name="transactions",
        postgresql_using="gin",
        postgresql_ops={"customer_email": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_transactions_anchor_unrated",
        table_name="transactions",
        postgresql_where=sa.text("anchor_unrated IS TRUE"),
    )
    op.drop_index(
        "ix_event_outbox_unprocessed",
        table_name="event_outbox",
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.create_index(
        op.f("ix_event_outbox_processed_at"), "event_outbox", ["processed_at"], unique=False
    )
    op.create_index(
        op.f("ix_event_outbox_event_type"), "event_outbox", ["event_type"], unique=False
    )
    op.create_index(op.f("ix_event_outbox_category"), "event_outbox", ["category"], unique=False)
    op.create_index(
        op.f("ix_customer_notifications_user_id"),
        "customer_notifications",
        ["user_id"],
        unique=False,
    )
    op.drop_index("ix_broadcast_recipients_status_channel_id", table_name="broadcast_recipients")
    op.create_index(
        op.f("ix_broadcast_recipients_status"),
        "broadcast_recipients",
        ["broadcast_id", "status"],
        unique=False,
    )
