from typing import Optional

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.db.encrypted_string import EncryptedString
from app.core.db.timestamp import TimestampMixin


class BotSettings(BaseSql, TimestampMixin):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    token: Mapped[Optional[str]] = mapped_column(EncryptedString())
    secret_token: Mapped[Optional[str]] = mapped_column(EncryptedString())
    # may carry proxy credentials, hence encrypted like the token
    proxy_url: Mapped[Optional[str]] = mapped_column(EncryptedString())

    # reported by the telegram process on start, cleared with a new token
    username: Mapped[Optional[str]] = mapped_column(String(32))
    # sha256 of the command menu last accepted by Telegram, so an unchanged one isn't resent
    commands_hash: Mapped[Optional[str]] = mapped_column(String(64))

    drop_pending_updates: Mapped[bool] = mapped_column(
        Boolean(), default=False, server_default="false"
    )
    # /start sends a welcome that takes away a reply keyboard left by a previous bot
    remove_reply_keyboard: Mapped[bool] = mapped_column(
        Boolean(), default=False, server_default="false"
    )
