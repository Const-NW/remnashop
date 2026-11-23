import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Optional
from uuid import UUID

from remnapy.enums import TrafficLimitStrategy

from app.cli.legacy.report import MigrationReport
from app.core.constants import (
    GRACE_DEFAULT_DURATION_DAYS,
    PANEL_TAG_MAX_LENGTH,
    PANEL_TAG_PATTERN,
)
from app.core.db.singleton import SINGLETON_ID
from app.core.enums import (
    AccessMode,
    Currency,
    ReferralAccrualStrategy,
    ReferralRewardStrategy,
    ReferralRewardType,
)

_ACCRUAL: Final[Mapping[str, ReferralAccrualStrategy]] = {
    "ON_FIRST_PAYMENT": ReferralAccrualStrategy.FIRST_PAYMENT,
    "ON_EACH_PAYMENT": ReferralAccrualStrategy.EVERY_PAYMENT,
}
_TELEGRAM_HOSTS: Final[tuple[str, ...]] = ("t.me/", "telegram.me/", "telegram.dog/")


@dataclass(frozen=True, kw_only=True)
class SettingsRows:
    access: dict[str, Any]
    bot_requirements: dict[str, Any]
    referral: dict[str, Any]
    grace: dict[str, Any]
    anchor_currency: Currency


def _channel_url(link: Optional[str]) -> Optional[str]:
    # the bot builds a url button from it, which needs a full link
    link = (link or "").strip()
    if not link:
        return None
    if link.startswith("@"):
        return f"https://t.me/{link[1:]}"
    if "://" not in link and link.startswith(_TELEGRAM_HOSTS):
        return f"https://{link}"
    return link


def _grace(row: Mapping[str, Any], report: MigrationReport) -> dict[str, Any]:
    # settings.grace came with 0042, an older database has no column
    grace = row.get("grace") or {}
    tag = grace.get("tag") or None
    if tag is not None and (
        len(tag) > PANEL_TAG_MAX_LENGTH or not re.fullmatch(PANEL_TAG_PATTERN, tag)
    ):
        report.notes.append(f"settings: grace tag '{tag}' is not a valid panel tag, dropped")
        tag = None
    external_squad = grace.get("external_squad")
    duration = grace.get("duration_days")
    return {
        "id": SINGLETON_ID,
        "enabled": bool(grace.get("enabled", False)),
        "internal_squads": [UUID(str(squad)) for squad in grace.get("internal_squads") or []],
        "external_squad": UUID(str(external_squad)) if external_squad else None,
        "traffic_limit_mb": int(grace.get("traffic_mb") or 0),
        "traffic_limit_strategy": TrafficLimitStrategy(
            grace.get("traffic_strategy") or TrafficLimitStrategy.NO_RESET
        ),
        "tag": tag,
        # a missing value is the new app's default; 0 (endless) only when set so
        "duration_days": GRACE_DEFAULT_DURATION_DAYS if duration is None else int(duration),
    }


def transform_settings(row: Mapping[str, Any], report: MigrationReport) -> SettingsRows:
    access = row["access"]
    requirements = row["requirements"]
    referral = row["referral"]
    reward = referral["reward"]

    if requirements.get("rules_link") or requirements.get("rules_required"):
        report.notes.append(
            "settings: rules link and rules_required are not migrated, "
            "documents are managed in the dashboard"
        )

    return SettingsRows(
        access={
            "id": SINGLETON_ID,
            "mode": AccessMode(access["mode"]),
            # 0018_restructure wrote purchases_allowed, the 0.7+ app saves payments_allowed
            "payments_enabled": access.get(
                "payments_allowed", access.get("purchases_allowed", True)
            ),
            "registration_enabled": access["registration_allowed"],
        },
        bot_requirements={
            "id": SINGLETON_ID,
            "channel_required": requirements["channel_required"],
            "channel_id": requirements.get("channel_id") or None,
            "channel_url": _channel_url(requirements.get("channel_link")),
        },
        referral={
            "id": SINGLETON_ID,
            "enabled": referral["enable"],
            "max_level": referral["level"],
            "accrual_strategy": _ACCRUAL[referral["accrual_strategy"]],
            "reward_type": ReferralRewardType(reward["type"]),
            "reward_strategy": ReferralRewardStrategy(reward["strategy"]),
            "reward_amounts": {
                str(level): str(amount) for level, amount in reward["config"].items()
            },
        },
        grace=_grace(row, report),
        anchor_currency=Currency(row["default_currency"]),
    )
