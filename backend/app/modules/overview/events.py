from dataclasses import dataclass, field
from typing import ClassVar

from app.core.events import EventCategory, SystemEvent


@dataclass(frozen=True, kw_only=True)
class RemnawaveUnavailableEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.INFRASTRUCTURE
    error: str


@dataclass(frozen=True, kw_only=True)
class RemnawaveRestoredEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.INFRASTRUCTURE


@dataclass(frozen=True, kw_only=True)
class RemnawaveVersionMismatchEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.INFRASTRUCTURE
    version: str
    supported: str


@dataclass(frozen=True, kw_only=True)
class SystemHealthDegradedEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.INFRASTRUCTURE
    kind: str
    subject: str
    value: int


@dataclass(frozen=True, kw_only=True)
class SystemHealthRestoredEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.INFRASTRUCTURE
    kind: str
    subject: str


@dataclass(frozen=True, kw_only=True)
class DailyReportEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.INFRASTRUCTURE
    # the local day the numbers cover, ISO date
    day: str
    new_users: int
    new_trials: int
    trial_conversions: int
    new_payers: int
    # anchor currency
    currency: str
    revenue: str
    active_trials: int
    active_paid: int
    purchases: int
    purchases_amount: str
    renewals: int
    renewals_amount: str
    changes: int
    changes_amount: str
    paid_never_connected: int
    # "@username" or the name, with the number invited that day; best first
    # (user id, name, invited); an empty name shows as "User #id"
    top_referrers: list[tuple[int, str, int]] = field(default_factory=list)
