from pathlib import Path
from typing import Final

import pytest
from fluent_compiler.bundle import FluentBundle

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
