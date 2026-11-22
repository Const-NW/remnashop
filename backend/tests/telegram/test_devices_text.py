from pathlib import Path
from typing import Final

import pytest
from fluent_compiler.bundle import FluentBundle

from app.telegram.dialogs.rendering import render_window_text

_LOCALES_DIR: Final[Path] = Path(__file__).parents[2] / "assets" / "translations"


@pytest.mark.parametrize(
    ("locale", "unlimited", "limited"),
    [
        ("en", "Your devices (3)", "Your devices (3/5)"),
        ("ru", "Ваши устройства (3)", "Ваши устройства (3/5)"),
    ],
)
def test_devices_title_leaves_out_an_unlimited_device_limit(
    locale: str, unlimited: str, limited: str
) -> None:
    bundle = FluentBundle.from_string(
        locale, (_LOCALES_DIR / locale / "menu.ftl").read_text(), use_isolating=False
    )
    for limit, expected in ((0, unlimited), (5, limited)):
        value, errors = bundle.format(
            "msg-menu-devices", {"devices_current": 3, "devices_max": limit}
        )
        assert (value, errors) == (expected, [])


class _Runner:
    def __init__(self, bundle: FluentBundle) -> None:
        self._bundle = bundle

    def get(self, key: str, **kwargs: object) -> str:
        value, errors = self._bundle.format(key, kwargs)
        assert errors == []
        return value


@pytest.mark.parametrize(
    ("locale", "named", "unnamed"),
    [
        (
            "en",
            "<b>Mine</b>\nModel: iPhone 15\nSystem: iOS 18.1\nApp: Happ 2.1.0\n"
            "Connected: 12.11.2026",
            "<b>iPhone 15</b>\nSystem: iOS\nConnected: 12.11.2026",
        ),
        (
            "ru",
            "<b>Mine</b>\nМодель: iPhone 15\nСистема: iOS 18.1\nПриложение: Happ 2.1.0\n"
            "Подключено: 12.11.2026",
            "<b>iPhone 15</b>\nСистема: iOS\nПодключено: 12.11.2026",
        ),
    ],
)
def test_device_card_drops_the_lines_it_has_nothing_for(
    locale: str, named: str, unnamed: str
) -> None:
    runner = _Runner(
        FluentBundle.from_string(
            locale, (_LOCALES_DIR / locale / "menu.ftl").read_text(), use_isolating=False
        )
    )
    full = {
        "device_name": "Mine",
        "device_model": "iPhone 15",
        "device_system": "iOS 18.1",
        "device_app": "Happ 2.1.0",
        "device_created_at": "12.11.2026",
        "device_created_at_local": "12.11.2026",
    }
    bare = {**full, "device_name": "iPhone 15", "device_model": "", "device_system": "iOS"}
    bare["device_app"] = ""

    assert render_window_text(runner, "msg-menu-device", **full) == named  # type: ignore[arg-type]
    assert render_window_text(runner, "msg-menu-device", **bare) == unnamed  # type: ignore[arg-type]
