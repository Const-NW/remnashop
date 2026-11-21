from datetime import datetime
from typing import Any, Optional

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.events import EventCategory


class EventOutboxEntry(BaseSql):
    __tablename__ = "event_outbox"
    __table_args__ = (
        # the hottest insert in the app: only the queue of unprocessed rows is
        # indexed, the daily prune of processed ones can afford a scan
        Index(
            "ix_event_outbox_unprocessed",
            "id",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    category: Mapped[EventCategory]
    event_type: Mapped[str] = mapped_column(String(128))
    occurred_at: Mapped[datetime]

    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    actor_login: Mapped[Optional[str]] = mapped_column(String(64))
    actor_name: Mapped[Optional[str]] = mapped_column(String())
    actor_email: Mapped[Optional[str]] = mapped_column(String())

    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

    processed_at: Mapped[Optional[datetime]]
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[Optional[str]] = mapped_column(String())
