from typing import Final, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1008"
down_revision: Union[str, None] = "1007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Button labels live under btn-*, so the translations page gives them the button
# icon picker instead of inserting premium emoji markup into the label.
_CUSTOMER_BUTTON: Final[str] = "btn-customer-notification"
# old key's name -> the glyph the shipped label used to start with, now its -icon
_CUSTOMER_ICONS: Final[dict[str, str]] = {
    "renew": "🔄",
    "buy": "💳",
    "close": "❌",
    "connect": "🚀",
    "support": "📩",
    "revoke-sessions": "🔒",
}
_INVITE_ATTRIBUTES: Final[tuple[str, ...]] = ("start", "start-icon", "start-icon-id")


def upgrade() -> None:
    for name, glyph in _CUSTOMER_ICONS.items():
        op.execute(
            sa.text(
                "UPDATE bot_translations SET message_id = :message_id, attribute_id = :name "
                "WHERE message_id = :old AND attribute_id = ''"
            ).bindparams(message_id=_CUSTOMER_BUTTON, name=name, old=f"ntf-customer-button-{name}")
        )
        # An edited label keeps its own text (and whatever glyph it has), so its
        # icon starts empty; an untouched one moves to the shipped label + icon.
        op.execute(
            sa.text(
                "INSERT INTO bot_translations (locale, message_id, attribute_id, value, default_value) "
                "SELECT locale, message_id, :icon, "
                "CASE WHEN value = default_value THEN :glyph ELSE '' END, :glyph "
                "FROM bot_translations WHERE message_id = :message_id AND attribute_id = :name "
                "ON CONFLICT DO NOTHING"
            ).bindparams(icon=f"{name}-icon", glyph=glyph, message_id=_CUSTOMER_BUTTON, name=name)
        )
    op.execute(
        sa.text(
            "UPDATE bot_translations SET message_id = 'btn-inline-invite' "
            "WHERE message_id = 'inline-invite' AND attribute_id = ANY(:names)"
        ).bindparams(names=list(_INVITE_ATTRIBUTES))
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE bot_translations SET message_id = 'inline-invite' "
            "WHERE message_id = 'btn-inline-invite' AND attribute_id = ANY(:names)"
        ).bindparams(names=list(_INVITE_ATTRIBUTES))
    )
    for name in _CUSTOMER_ICONS:
        op.execute(
            sa.text(
                "DELETE FROM bot_translations "
                "WHERE message_id = :message_id AND attribute_id LIKE :icons"
            ).bindparams(message_id=_CUSTOMER_BUTTON, icons=f"{name}-icon%")
        )
        op.execute(
            sa.text(
                "UPDATE bot_translations SET message_id = :old, attribute_id = '' "
                "WHERE message_id = :message_id AND attribute_id = :name"
            ).bindparams(old=f"ntf-customer-button-{name}", message_id=_CUSTOMER_BUTTON, name=name)
        )
