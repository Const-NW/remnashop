from typing import Any, Final, Optional

from app.core.enums import AppearanceImageKind, AppearanceTarget
from app.modules.settings.dto import AppearanceSettingsDto

_DEFAULT_NAME: Final[str] = "Remnashop"
_DEFAULT_COLOR: Final[str] = "#111111"
# the browser sees the cabinet's API under /api (its nginx rewrites to /api/v1)
_API_PREFIX: Final[dict[AppearanceTarget, str]] = {
    AppearanceTarget.CABINET: "/api",
    AppearanceTarget.DASHBOARD: "/api/v1",
}
_START_URL: Final[dict[AppearanceTarget, str]] = {
    AppearanceTarget.CABINET: "/",
    AppearanceTarget.DASHBOARD: "/dashboard",
}
# both frontends ship it in public/; its mark sits inside the maskable safe zone,
# an uploaded favicon is often edge to edge and would be cropped
_DEFAULT_ICONS: Final[list[dict[str, str]]] = [
    {"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    {"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"},
]


def _image_path(
    target: AppearanceTarget, kind: AppearanceImageKind, dto: AppearanceSettingsDto
) -> str:
    version = int(dto.updated_at.timestamp()) if dto.updated_at else 0
    return f"{_API_PREFIX[target]}/appearance/{target}/{kind}?v={version}"


def push_icon_path(target: AppearanceTarget, settings: AppearanceSettingsDto) -> Optional[str]:
    if settings.favicon_content_type is None:
        return None
    return _image_path(target, AppearanceImageKind.FAVICON, settings)


def appearance_manifest(
    target: AppearanceTarget, settings: AppearanceSettingsDto
) -> dict[str, Any]:
    name = settings.brand_name or _DEFAULT_NAME
    # one icon: iOS picks a sized icon over an uploaded "any" one listed with it
    icons = (
        [
            {
                "src": _image_path(target, AppearanceImageKind.FAVICON, settings),
                "sizes": "any",
                "type": settings.favicon_content_type,
            }
        ]
        if settings.favicon_content_type
        else _DEFAULT_ICONS
    )
    return {
        "name": name,
        "short_name": name,
        "start_url": _START_URL[target],
        "scope": "/",
        "display": "standalone",
        "background_color": _DEFAULT_COLOR,
        "theme_color": settings.primary_color or _DEFAULT_COLOR,
        "icons": icons,
    }
