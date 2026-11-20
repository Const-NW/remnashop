from collections.abc import Iterable
from enum import Enum
from typing import Any, Final, Optional

from app.modules.dialogs.dto import DialogButtonDto

_LAYOUT_FIELDS: Final[tuple[str, ...]] = (
    "row",
    "column",
    "text_key",
    "link_type",
    "action_key",
    "target_window_key",
    "url_value",
    "visibility_condition",
    "style",
    "is_hidden",
)

Layout = set[tuple[object, ...]]


def snapshot_conditions(snapshot: dict[str, Any]) -> dict[str, Optional[str]]:
    # a seed window's catalog buttons, by text key, with the condition the catalog gave them
    return {b["text_key"]: b.get("visibility_condition") for b in snapshot.get("buttons", [])}


def snapshot_required_keys(snapshot: dict[str, Any]) -> set[str]:
    return {b["text_key"] for b in snapshot.get("buttons", []) if b.get("required")}


def snapshot_layout(snapshot: dict[str, Any]) -> Layout:
    defaults: dict[str, object] = {"is_hidden": False}
    return {
        tuple(raw.get(name, defaults.get(name)) for name in _LAYOUT_FIELDS)
        for raw in snapshot.get("buttons", [])
    }


def buttons_layout(buttons: Iterable[DialogButtonDto]) -> Layout:
    return {
        tuple(
            value.value if isinstance(value, Enum) else value
            for value in (getattr(button, name) for name in _LAYOUT_FIELDS)
        )
        for button in buttons
    }
