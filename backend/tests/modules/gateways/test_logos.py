from pathlib import Path
from typing import Final

import app as app_package
from app.core.enums import PaymentGatewayType

_FRONTEND_SRC: Final[Path] = Path(app_package.__file__).parents[2] / "frontend" / "src"
_LOGOS_DIR: Final[Path] = _FRONTEND_SRC / "features" / "gateway-settings" / "logos"


def test_every_gateway_has_one_logo() -> None:
    logos = [path.stem for path in _LOGOS_DIR.iterdir() if path.suffix in {".png", ".svg"}]

    assert len(logos) == len(set(logos)), "a gateway has both a .png and a .svg logo"
    assert set(logos) == {gateway.value for gateway in PaymentGatewayType}
