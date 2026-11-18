import asyncio
import json
from typing import Final

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.dependencies.public import get_current_user
from app.api.errors import error_responses
from app.core.config import AppConfig
from app.core.enums import AppearanceImageKind, AppearanceTarget
from app.core.error_codes import ErrorCode
from app.core.exceptions import NotFoundError
from app.modules.settings.api.schemas import AppearanceResponse, CabinetSettingsResponse
from app.modules.settings.branding import (
    appearance_manifest,
    link_preview_html,
    link_preview_image,
)
from app.modules.settings.usecases import (
    GetCabinetSettingsUseCase,
    GetPublicAppearanceImageUseCase,
    GetPublicAppearanceUseCase,
)
from app.modules.users import UserDto

router = APIRouter(prefix="/appearance", tags=["appearance"])

# Uploads may be SVG: opened directly, the file must not run scripts or load
# anything, and must never be sniffed into another type.
_IMAGE_HEADERS: Final[dict[str, str]] = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}

# both frontends ship it in public/
_DEFAULT_FAVICON_PATH: Final[str] = "/favicon.svg"


@router.get("/{target}/manifest.webmanifest")
@inject
async def get_appearance_manifest(
    target: AppearanceTarget, use_case: FromDishka[GetPublicAppearanceUseCase]
) -> Response:
    manifest = appearance_manifest(target, await use_case.execute(target))
    return Response(
        json.dumps(manifest),
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/cabinet/link-preview", response_class=HTMLResponse)
@inject
async def get_cabinet_link_preview(
    use_case: FromDishka[GetPublicAppearanceUseCase], config: FromDishka[AppConfig]
) -> HTMLResponse:
    settings = await use_case.execute(AppearanceTarget.CABINET)
    return HTMLResponse(link_preview_html(settings, config.cabinet.url))


@router.get("/cabinet/preview-image", responses=error_responses(404))
@inject
async def get_cabinet_preview_image(
    use_case: FromDishka[GetPublicAppearanceImageUseCase],
) -> Response:
    logo = await use_case.execute(AppearanceTarget.CABINET, AppearanceImageKind.LOGO)
    image = await asyncio.to_thread(link_preview_image, logo)
    if image is None:
        raise NotFoundError("Logo has no preview", code=ErrorCode.APPEARANCE_IMAGE_NOT_SET)
    return Response(
        content=image.data,
        media_type=image.content_type,
        # the URL carries the logo's version
        headers={**_IMAGE_HEADERS, "Cache-Control": "public, max-age=86400"},
    )


@router.get("/{target}", response_model=AppearanceResponse)
@inject
async def get_appearance(
    target: AppearanceTarget, use_case: FromDishka[GetPublicAppearanceUseCase]
) -> AppearanceResponse:
    settings = await use_case.execute(target)
    return AppearanceResponse.model_validate(settings, from_attributes=True)


@router.get("/{target}/logo", responses=error_responses(404))
@inject
async def get_appearance_logo(
    target: AppearanceTarget, use_case: FromDishka[GetPublicAppearanceImageUseCase]
) -> Response:
    image = await use_case.execute(target, AppearanceImageKind.LOGO)
    return Response(content=image.data, media_type=image.content_type, headers=_IMAGE_HEADERS)


@router.get("/{target}/favicon", responses=error_responses(404))
@inject
async def get_appearance_favicon(
    target: AppearanceTarget, use_case: FromDishka[GetPublicAppearanceImageUseCase]
) -> Response:
    image = await use_case.execute(target, AppearanceImageKind.FAVICON)
    return Response(content=image.data, media_type=image.content_type, headers=_IMAGE_HEADERS)


@router.get("/{target}/app-icon", responses=error_responses(404))
@inject
async def get_appearance_app_icon(
    target: AppearanceTarget, use_case: FromDishka[GetPublicAppearanceImageUseCase]
) -> Response:
    image = await use_case.execute(target, AppearanceImageKind.APP_ICON)
    return Response(content=image.data, media_type=image.content_type, headers=_IMAGE_HEADERS)


# Linked from index.html: Safari takes the tab icon from the page as loaded and
# ignores a <link rel="icon"> changed by a script.
@router.get("/{target}/tab-icon")
@inject
async def get_appearance_tab_icon(
    target: AppearanceTarget, use_case: FromDishka[GetPublicAppearanceImageUseCase]
) -> Response:
    no_cache = {"Cache-Control": "no-cache"}
    try:
        image = await use_case.execute(target, AppearanceImageKind.FAVICON)
    except NotFoundError:
        return RedirectResponse(
            _DEFAULT_FAVICON_PATH, status_code=status.HTTP_307_TEMPORARY_REDIRECT, headers=no_cache
        )
    return Response(
        content=image.data, media_type=image.content_type, headers={**_IMAGE_HEADERS, **no_cache}
    )


shop_router = APIRouter(prefix="/shop", tags=["shop"])


@shop_router.get("/public-settings", response_model=CabinetSettingsResponse)
@inject
async def get_public_shop_settings(
    use_case: FromDishka[GetCabinetSettingsUseCase],
    actor: UserDto = Depends(get_current_user),
) -> CabinetSettingsResponse:
    return CabinetSettingsResponse.model_validate(await use_case(actor), from_attributes=True)
