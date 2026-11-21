from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1027"
down_revision: Union[str, None] = "1026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KIND = postgresql.ENUM("PURCHASE", "CARRIED", "BONUS", "LEGACY", name="creditsegmentkind")
_CREDIT_KEYS = (
    "credit_paid",
    "credit_days",
    "unlimited_from",
    "unlimited_from_credit_paid",
    "unlimited_from_credit_days",
)
_PAID = (
    "COALESCE((plan_snapshot->>'credit_paid')::numeric, (plan_snapshot->>'price_paid')::numeric)"
)
_DAYS = "COALESCE((plan_snapshot->>'credit_days')::int, (plan_snapshot->>'duration_days')::int)"


def upgrade() -> None:
    op.add_column(
        "shop_settings",
        sa.Column(
            "plan_change_traffic_cap_enabled", sa.Boolean(), server_default="true", nullable=False
        ),
    )
    _KIND.create(op.get_bind())
    op.create_table(
        "credit_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind", postgresql.ENUM(name="creditsegmentkind", create_type=False), nullable=False
        ),
        sa.Column("payment_key", sa.String(), nullable=True),
        sa.Column("traffic_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "traffic_pace", sa.Numeric(precision=7, scale=6), server_default="1", nullable=False
        ),
        sa.Column("paid", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("currency", postgresql.ENUM(name="currency", create_type=False), nullable=False),
        sa.Column("seconds", sa.BigInteger(), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_expire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.CheckConstraint("paid >= 0", name=op.f("ck_credit_segments_paid_non_negative")),
        sa.CheckConstraint("seconds >= 0", name=op.f("ck_credit_segments_seconds_non_negative")),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
            name=op.f("fk_credit_segments_subscription_id_subscriptions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["credit_segments.id"],
            name=op.f("fk_credit_segments_superseded_by_id_credit_segments"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_segments")),
    )
    op.create_index(
        op.f("ix_credit_segments_subscription_id"),
        "credit_segments",
        ["subscription_id"],
        unique=False,
    )
    # every subscription, deleted ones too: a refunded plan change puts the replaced
    # row back, credit included. A fixed term is one segment ending at its expiry
    op.execute(
        "INSERT INTO credit_segments "
        "(subscription_id, kind, paid, currency, seconds, ends_at) "
        f"SELECT id, 'LEGACY', {_PAID}, (plan_snapshot->>'currency')::currency, "
        f"{_DAYS}::bigint * 86400, expire_at FROM subscriptions WHERE {_DAYS} > 0"
    )
    # an unlimited term, and the fixed one it replaced, superseded until its refund
    op.execute(
        "WITH unlimited AS ("
        "INSERT INTO credit_segments "
        "(subscription_id, kind, paid, currency, seconds, replaced_expire_at) "
        f"SELECT id, 'LEGACY', {_PAID}, (plan_snapshot->>'currency')::currency, 0, "
        "(plan_snapshot->>'unlimited_from')::timestamptz "
        f"FROM subscriptions WHERE {_DAYS} <= 0 "
        "RETURNING id, subscription_id) "
        "INSERT INTO credit_segments "
        "(subscription_id, kind, paid, currency, seconds, ends_at, superseded_by_id) "
        "SELECT s.id, 'LEGACY', (s.plan_snapshot->>'unlimited_from_credit_paid')::numeric, "
        "(s.plan_snapshot->>'currency')::currency, "
        "COALESCE((s.plan_snapshot->>'unlimited_from_credit_days')::bigint, 0) * 86400, "
        "(s.plan_snapshot->>'unlimited_from')::timestamptz, unlimited.id "
        "FROM unlimited JOIN subscriptions s ON s.id = unlimited.subscription_id "
        "WHERE s.plan_snapshot->>'unlimited_from_credit_paid' IS NOT NULL"
    )
    op.execute(
        "UPDATE subscriptions SET plan_snapshot = plan_snapshot "
        + "".join(f" - '{key}'" for key in _CREDIT_KEYS)
    )


def downgrade() -> None:
    # the credit back into the snapshot, its amounts summed as they stand (segments in
    # another currency included as is): what's live, and what an unlimited term replaced
    op.execute(
        "UPDATE subscriptions s SET plan_snapshot = s.plan_snapshot || jsonb_build_object("
        "'credit_paid', live.paid::text, 'credit_days', live.days) "
        "FROM (SELECT subscription_id, sum(paid) AS paid, (sum(seconds) / 86400)::int AS days "
        "FROM credit_segments WHERE superseded_by_id IS NULL "
        # a term lapsed before the last renewal started the old credit afresh
        "AND (ends_at IS NULL OR ends_at > now()) GROUP BY subscription_id) live "
        "WHERE live.subscription_id = s.id"
    )
    op.execute(
        "UPDATE subscriptions s SET plan_snapshot = s.plan_snapshot || jsonb_build_object("
        "'unlimited_from', u.replaced_expire_at, "
        "'unlimited_from_credit_paid', replaced.paid::text, "
        "'unlimited_from_credit_days', replaced.days) "
        "FROM credit_segments u "
        "LEFT JOIN (SELECT superseded_by_id, sum(paid) AS paid, "
        "(sum(seconds) / 86400)::int AS days FROM credit_segments "
        "WHERE superseded_by_id IS NOT NULL GROUP BY superseded_by_id) replaced "
        "ON replaced.superseded_by_id = u.id "
        "WHERE u.subscription_id = s.id AND u.ends_at IS NULL AND u.superseded_by_id IS NULL "
        "AND u.replaced_expire_at IS NOT NULL"
    )
    op.drop_index(
        op.f("ix_credit_segments_subscription_id"),
        table_name="credit_segments",
    )
    op.drop_table("credit_segments")
    _KIND.drop(op.get_bind())
    op.drop_column("shop_settings", "plan_change_traffic_cap_enabled")
