from typing import Any, Final, Optional

from app.core.enums import AppearanceImageKind, AppearanceTarget
from app.modules.settings.dto import AppearanceSettingsDto

_DEFAULT_NAME: Final[str] = "Remnashop"
_DEFAULT_COLOR: Final[str] = "#111111"
_SVG: Final[str] = "image/svg+xml"
# the browser sees the cabinet's API under /api (its nginx rewrites to /api/v1)
_CABINET_API: Final[str] = "/api"
_DASHBOARD_API: Final[str] = "/api/v1"
_DASHBOARD_START_URL: Final[str] = "/dashboard"
_DEFAULT_ICONS: Final[list[dict[str, str]]] = [
    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
]

_Sources = list[tuple[AppearanceTarget, AppearanceSettingsDto]]


def _images(sources: _Sources, api_prefix: str, *, svg: bool) -> list[tuple[str, str]]:
    """(path, content type) of branding images, favicons first, in source order."""
    images: list[tuple[str, str]] = []
    for kind in (AppearanceImageKind.FAVICON, AppearanceImageKind.LOGO):
        for target, dto in sources:
            content_type = (
                dto.favicon_content_type
                if kind is AppearanceImageKind.FAVICON
                else dto.logo_content_type
            )
            if content_type is None or (content_type == _SVG and not svg) or dto.updated_at is None:
                continue
            version = int(dto.updated_at.timestamp())
            images.append((f"{api_prefix}/appearance/{target}/{kind}?v={version}", content_type))
    return images


def push_icon_path(
    cabinet: AppearanceSettingsDto, dashboard: AppearanceSettingsDto
) -> Optional[str]:
    sources = [(AppearanceTarget.CABINET, cabinet), (AppearanceTarget.DASHBOARD, dashboard)]
    # notifications don't render SVG
    images = _images(sources, _CABINET_API, svg=False)
    return images[0][0] if images else None


def appearance_manifest(
    target: AppearanceTarget, cabinet: AppearanceSettingsDto, dashboard: AppearanceSettingsDto
) -> dict[str, Any]:
    """Web app manifest; what the cabinet leaves unset comes from the dashboard."""
    if target is AppearanceTarget.CABINET:
        sources = [(AppearanceTarget.CABINET, cabinet), (AppearanceTarget.DASHBOARD, dashboard)]
        api_prefix, start_url = _CABINET_API, "/"
    else:
        sources = [(AppearanceTarget.DASHBOARD, dashboard)]
        api_prefix, start_url = _DASHBOARD_API, _DASHBOARD_START_URL
    name = next((dto.brand_name for _, dto in sources if dto.brand_name), _DEFAULT_NAME)
    color = next((dto.primary_color for _, dto in sources if dto.primary_color), _DEFAULT_COLOR)
    return {
        "name": name,
        "short_name": name,
        "start_url": start_url,
        "scope": "/",
        "display": "standalone",
        "background_color": _DEFAULT_COLOR,
        "theme_color": color,
        "icons": [
            *(
                {"src": path, "sizes": "any", "type": ct}
                for path, ct in _images(sources, api_prefix, svg=True)
            ),
            *_DEFAULT_ICONS,
        ],
    }
