from typing import Optional

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import BaseSql
from app.core.enums import Locale


class PlanTranslation(BaseSql):
    __tablename__ = "plan_translations"

    plan_id: Mapped[int] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[Locale] = mapped_column(primary_key=True)

    name: Mapped[str]
    description: Mapped[Optional[str]]
