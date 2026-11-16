from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.db.timestamp import NOW_FUNC
from app.core.enums import RegistrationSource


class UserRegistration(BaseSql):
    """What was known about a customer at sign-up; written once, never updated."""

    __tablename__ = "user_registrations"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[RegistrationSource]
    # the whole /start argument, kept even when no code in it resolves
    start_payload: Mapped[Optional[str]] = mapped_column(String(64))
    telegram_language_code: Mapped[Optional[str]] = mapped_column(String(16))
    telegram_is_premium: Mapped[Optional[bool]]
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    # first cabinet visit as the browser reported it
    landing_url: Mapped[Optional[str]] = mapped_column(String(512))
    referer: Mapped[Optional[str]] = mapped_column(String(512))
    utm_source: Mapped[Optional[str]] = mapped_column(String(128))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(128))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(128))
    utm_content: Mapped[Optional[str]] = mapped_column(String(128))
    utm_term: Mapped[Optional[str]] = mapped_column(String(128))
    click_id: Mapped[Optional[str]] = mapped_column(String(256))
    platform: Mapped[Optional[str]] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=NOW_FUNC)
