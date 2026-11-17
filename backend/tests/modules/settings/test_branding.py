from datetime import datetime, timezone
from typing import Optional

from app.core.enums import AppearanceTarget
from app.modules.settings import AppearanceSettingsDto
from app.modules.settings.branding import appearance_manifest, push_icon_path

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_V = int(_AT.timestamp())
_DEFAULT_ICONS = [
    {"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    {"src": "/app-icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"},
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


def test_push_icon_is_the_favicon_of_any_format() -> None:
    cabinet = _dto(favicon="image/svg+xml", logo="image/png")

    assert (
        push_icon_path(AppearanceTarget.CABINET, cabinet)
        == f"/api/appearance/cabinet/favicon?v={_V}"
    )


def test_push_icon_ignores_logo() -> None:
    assert push_icon_path(AppearanceTarget.DASHBOARD, _dto(logo="image/png")) is None


def test_push_icon_none_without_images() -> None:
    assert push_icon_path(AppearanceTarget.CABINET, _dto()) is None


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
