from datetime import datetime, timezone
from typing import Optional

from app.core.enums import AppearanceTarget
from app.modules.settings import AppearanceSettingsDto
from app.modules.settings.branding import appearance_manifest, push_icon_path

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_V = int(_AT.timestamp())
_DEFAULT_ICONS = [
    {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
]


def _dto(
    *,
    logo: Optional[str] = None,
    favicon: Optional[str] = None,
    brand_name: Optional[str] = None,
    primary_color: Optional[str] = None,
) -> AppearanceSettingsDto:
    return AppearanceSettingsDto(
        brand_name=brand_name,
        primary_color=primary_color,
        has_logo=logo is not None,
        has_favicon=favicon is not None,
        logo_content_type=logo,
        favicon_content_type=favicon,
        updated_at=_AT,
    )


def test_push_icon_prefers_favicon() -> None:
    cabinet = _dto(favicon="image/png", logo="image/png")

    assert push_icon_path(cabinet) == f"/api/appearance/cabinet/favicon?v={_V}"


def test_push_icon_skips_svg() -> None:
    cabinet = _dto(favicon="image/svg+xml", logo="image/png")

    assert push_icon_path(cabinet) == f"/api/appearance/cabinet/logo?v={_V}"


def test_push_icon_none_when_only_svg() -> None:
    assert push_icon_path(_dto(favicon="image/svg+xml", logo="image/svg+xml")) is None


def test_manifest_defaults_without_appearance() -> None:
    manifest = appearance_manifest(AppearanceTarget.CABINET, _dto())

    assert manifest["name"] == manifest["short_name"] == "Remnashop"
    assert manifest["theme_color"] == "#111111"
    assert manifest["icons"] == _DEFAULT_ICONS
    assert manifest["start_url"] == manifest["scope"] == "/"
    assert manifest["display"] == "standalone"


def test_manifest_uses_brand_color_and_favicon_only() -> None:
    settings = _dto(
        brand_name="Acme", primary_color="#ff0000", favicon="image/svg+xml", logo="image/png"
    )

    manifest = appearance_manifest(AppearanceTarget.CABINET, settings)

    assert manifest["name"] == manifest["short_name"] == "Acme"
    assert manifest["theme_color"] == "#ff0000"
    assert manifest["icons"] == [
        {"src": f"/api/appearance/cabinet/favicon?v={_V}", "sizes": "any", "type": "image/svg+xml"}
    ]


def test_logo_alone_keeps_default_icons() -> None:
    manifest = appearance_manifest(AppearanceTarget.CABINET, _dto(logo="image/png"))

    assert manifest["icons"] == _DEFAULT_ICONS


def test_dashboard_manifest_paths() -> None:
    manifest = appearance_manifest(AppearanceTarget.DASHBOARD, _dto(favicon="image/png"))

    assert manifest["start_url"] == "/dashboard"
    assert manifest["icons"][0]["src"] == f"/api/v1/appearance/dashboard/favicon?v={_V}"
