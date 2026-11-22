from dataclasses import dataclass
from typing import ClassVar, Literal, Optional

from app.core.events import ActorEvent, EventCategory, SystemEvent


@dataclass(frozen=True, kw_only=True)
class BroadcastChangedEvent(ActorEvent):
    category: ClassVar[EventCategory] = EventCategory.BROADCAST
    action: Literal[
        "add_email_image",
        "create",
        "delete",
        "delete_media",
        "duplicate",
        "retry",
        "set_media",
        "update",
    ]
    broadcast_id: int
    # set on a duplicate: the broadcast it was copied from
    source_broadcast_id: Optional[int] = None


@dataclass(frozen=True, kw_only=True)
class BroadcastScheduledEvent(ActorEvent):
    category: ClassVar[EventCategory] = EventCategory.BROADCAST
    broadcast_id: int
    scheduled_at: str


@dataclass(frozen=True, kw_only=True)
class BroadcastCanceledEvent(ActorEvent):
    category: ClassVar[EventCategory] = EventCategory.BROADCAST
    broadcast_id: int


@dataclass(frozen=True, kw_only=True)
class BroadcastRecalledEvent(ActorEvent):
    category: ClassVar[EventCategory] = EventCategory.BROADCAST
    broadcast_id: int


@dataclass(frozen=True, kw_only=True)
class BroadcastStartedEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.BROADCAST
    broadcast_id: int
    total: int
    promocode_dropped: bool


@dataclass(frozen=True, kw_only=True)
class BroadcastCompletedEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.BROADCAST
    broadcast_id: int
    sent: int
    failed: int
    blocked: int


@dataclass(frozen=True, kw_only=True)
class BroadcastFailedEvent(SystemEvent):
    category: ClassVar[EventCategory] = EventCategory.BROADCAST
    broadcast_id: int
    error: str
