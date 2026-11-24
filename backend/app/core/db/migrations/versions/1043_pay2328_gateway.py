from typing import Final, Sequence, Union

from alembic import op

revision: str = "1043"
down_revision: Union[str, None] = "1042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_LABELS: Final[tuple[str, ...]] = (
    "TELEGRAM_STARS",
    "YOOKASSA",
    "YOOMONEY",
    "VALUTIX",
    "CRYPTOMUS",
    "HELEKET",
    "CRYPTOPAY",
    "FREEKASSA",
    "MULENPAY",
    "PAYMASTER",
    "PLATEGA",
    "ROBOKASSA",
    "URLPAY",
    "WATA",
    "ROLLYPAY",
    "ANTILOPAY",
    "PAYPALYCH",
    "RIOPAY",
    "AURAPAY",
    "SEVERPAY",
    "LAVA",
    "CLOUDPAYMENTS",
    "PAYPEAR",
    "CISPAY",
)


def upgrade() -> None:
    op.execute("ALTER TYPE payment_gateway_type ADD VALUE IF NOT EXISTS 'PAY_2328'")


# Postgres can't drop an enum label: the type is rebuilt without it, which
# fails while a gateway row or a transaction still uses PAY_2328.
def downgrade() -> None:
    op.execute("DELETE FROM payment_gateways WHERE type = 'PAY_2328'")
    op.execute("ALTER TYPE payment_gateway_type RENAME TO payment_gateway_type_old")
    labels = ", ".join(f"'{label}'" for label in _PREVIOUS_LABELS)
    op.execute(f"CREATE TYPE payment_gateway_type AS ENUM ({labels})")
    for table, column in (("payment_gateways", "type"), ("transactions", "gateway_type")):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} "
            f"TYPE payment_gateway_type USING {column}::text::payment_gateway_type"
        )
    op.execute("DROP TYPE payment_gateway_type_old")
