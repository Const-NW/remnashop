from datetime import datetime, timezone
from typing import Optional

from app.core.enums import AppearanceTarget
from app.modules.settings import AppearanceSettingsDto
from app.modules.settings.branding import (
    appearance_manifest,
    link_preview_html,
    link_preview_image,
    push_icon_path,
)
from app.modules.settings.dto import AppearanceImageDto

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
    app_icon: Optional[str] = None,
    brand_name: Optional[str] = None,
    primary_color: Optional[str] = None,
) -> AppearanceSettingsDto:
    return AppearanceSettingsDto(
        brand_name=brand_name,
        primary_color=primary_color,
        has_logo=logo is not None,
        has_favicon=favicon is not None,
        has_app_icon=app_icon is not None,
        logo_content_type=logo,
        favicon_content_type=favicon,
        app_icon_content_type=app_icon,
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
    assert manifest["theme_color"] == manifest["background_color"] == "#111111"
    assert manifest["icons"] == _DEFAULT_ICONS
    assert manifest["start_url"] == manifest["scope"] == "/"
    assert manifest["display"] == "standalone"


def test_manifest_uses_brand_name_and_color() -> None:
    settings = _dto(brand_name="Acme", primary_color="#ff0000")

    manifest = appearance_manifest(AppearanceTarget.CABINET, settings)

    assert manifest["name"] == manifest["short_name"] == "Acme"
    assert manifest["theme_color"] == "#ff0000"


def test_favicon_and_logo_keep_default_icons() -> None:
    settings = _dto(favicon="image/svg+xml", logo="image/png")

    manifest = appearance_manifest(AppearanceTarget.CABINET, settings)

    assert manifest["icons"] == _DEFAULT_ICONS


def test_manifest_uses_the_app_icon() -> None:
    settings = _dto(app_icon="image/png", favicon="image/svg+xml")

    manifest = appearance_manifest(AppearanceTarget.CABINET, settings)

    assert manifest["icons"] == [
        {"src": f"/api/appearance/cabinet/app-icon?v={_V}", "sizes": "any", "type": "image/png"}
    ]
    assert "background_color" not in manifest


def test_dashboard_manifest_paths() -> None:
    manifest = appearance_manifest(AppearanceTarget.DASHBOARD, _dto(app_icon="image/png"))

    assert manifest["start_url"] == "/dashboard"
    assert manifest["icons"][0]["src"] == f"/api/v1/appearance/dashboard/app-icon?v={_V}"


def test_push_icon_ignores_the_app_icon() -> None:
    assert push_icon_path(AppearanceTarget.CABINET, _dto(app_icon="image/png")) is None


def test_link_preview_escapes_the_name_and_links_the_logo() -> None:
    settings = _dto(brand_name='A&B "VPN"', logo="image/svg+xml")

    preview = link_preview_html(settings, "https://cab.example.com")

    assert "<title>A&amp;B &quot;VPN&quot;</title>" in preview
    assert (
        f'<meta property="og:image" content="https://cab.example.com'
        f'/api/appearance/cabinet/preview-image?v={_V}" />'
    ) in preview


def test_link_preview_without_logo_or_domain_has_no_image() -> None:
    assert "og:image" not in link_preview_html(_dto(), "https://cab.example.com")
    assert "og:image" not in link_preview_html(_dto(logo="image/png"), "")
    assert "<title>Remnashop</title>" in link_preview_html(_dto(), "")


def test_preview_image_renders_svg_to_png() -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b'<rect width="10" height="10"/></svg>'
    )

    image = link_preview_image(AppearanceImageDto(data=svg, content_type="image/svg+xml"))

    assert image is not None
    assert image.content_type == "image/png"
    assert image.data.startswith(b"\x89PNG")


def test_preview_image_refuses_svg_reading_files() -> None:
    svg = (
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        b'<image href="/etc/hosts" width="10" height="10"/></svg>'
    )

    assert link_preview_image(AppearanceImageDto(data=svg, content_type="image/svg+xml")) is None
