import html
from functools import lru_cache
from typing import Any, Final, Optional

from app.core.enums import AppearanceImageKind, AppearanceTarget
from app.core.utils.image import svg_to_png
from app.modules.settings.dto import AppearanceImageDto, AppearanceSettingsDto

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
# both frontends ship it in public/; its mark sits inside the maskable safe zone
_DEFAULT_ICONS: Final[list[dict[str, str]]] = [
    {"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    {"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"},
]


_SVG_CONTENT_TYPE: Final[str] = "image/svg+xml"
_PREVIEW_IMAGE_SIDE: Final[int] = 512


def _version(dto: AppearanceSettingsDto) -> int:
    return int(dto.updated_at.timestamp()) if dto.updated_at else 0


def _image_path(
    target: AppearanceTarget, kind: AppearanceImageKind, dto: AppearanceSettingsDto
) -> str:
    segment = str(kind).replace("_", "-")
    return f"{_API_PREFIX[target]}/appearance/{target}/{segment}?v={_version(dto)}"


def push_icon_path(target: AppearanceTarget, settings: AppearanceSettingsDto) -> Optional[str]:
    if settings.favicon_content_type is None:
        return None
    return _image_path(target, AppearanceImageKind.FAVICON, settings)


def appearance_manifest(
    target: AppearanceTarget, settings: AppearanceSettingsDto
) -> dict[str, Any]:
    name = settings.brand_name or _DEFAULT_NAME
    manifest: dict[str, Any] = {
        "name": name,
        "short_name": name,
        "start_url": _START_URL[target],
        "scope": "/",
        "display": "standalone",
        "theme_color": settings.primary_color or _DEFAULT_COLOR,
    }
    if settings.app_icon_content_type is None:
        # the splash backdrop matches the default icon's own; an uploaded one gets the browser's
        manifest["background_color"] = _DEFAULT_COLOR
        manifest["icons"] = _DEFAULT_ICONS
    else:
        # one icon: iOS picks a sized icon over an uploaded "any" one listed with it
        manifest["icons"] = [
            {
                "src": _image_path(target, AppearanceImageKind.APP_ICON, settings),
                "sizes": "any",
                "type": settings.app_icon_content_type,
            }
        ]
    return manifest


# Spliced into the cabinet's index.html by its nginx (SSI): link previews read
# the page as served and run no scripts.
def link_preview_html(settings: AppearanceSettingsDto, cabinet_url: str) -> str:
    name = html.escape(settings.brand_name or _DEFAULT_NAME)
    tags = [f"<title>{name}</title>", f'<meta property="og:title" content="{name}" />']
    # an og:image URL has to be absolute
    if settings.logo_content_type is not None and cabinet_url:
        image = f"{cabinet_url}/api/appearance/cabinet/preview-image?v={_version(settings)}"
        tags.append(f'<meta property="og:image" content="{html.escape(image)}" />')
    return "\n".join(tags)


# messengers don't render SVG in a preview
def link_preview_image(logo: AppearanceImageDto) -> Optional[AppearanceImageDto]:
    if logo.content_type != _SVG_CONTENT_TYPE:
        return logo
    png = _svg_logo_png(logo.data)
    return None if png is None else AppearanceImageDto(data=png, content_type="image/png")


# one render per uploaded logo, not per crawler hit
@lru_cache(maxsize=4)
def _svg_logo_png(data: bytes) -> Optional[bytes]:
    return svg_to_png(data, _PREVIEW_IMAGE_SIDE)
