from typing import Optional

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.db.timestamp import TimestampMixin


class AppearanceSettings(BaseSql, TimestampMixin):
    __tablename__ = "appearance_settings"

    # one row per AppearanceTarget: 1 = dashboard, 2 = cabinet
    id: Mapped[int] = mapped_column(primary_key=True)

    brand_name: Mapped[Optional[str]] = mapped_column(String(64))
    primary_color: Mapped[Optional[str]] = mapped_column(String(7))
    # the background's base color; None: derived from the accent
    shader_base: Mapped[Optional[str]] = mapped_column(String(7))
    # 3 tones pinned by hand, None items derived from the base; set as one unit
    shader_tones: Mapped[Optional[list[Optional[str]]]] = mapped_column(JSONB)
    shader_enabled: Mapped[bool] = mapped_column(Boolean(), default=True, server_default="true")

    logo_content_type: Mapped[Optional[str]] = mapped_column(String(32))
    logo_extension: Mapped[Optional[str]] = mapped_column(String(8))
    favicon_content_type: Mapped[Optional[str]] = mapped_column(String(32))
    favicon_extension: Mapped[Optional[str]] = mapped_column(String(8))
