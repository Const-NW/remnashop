from typing import Optional

from app.core.enums import AppearanceImageKind, AppearanceTarget
from app.core.exceptions import NotFoundError
from app.modules.settings.dto import AppearanceImageDto
from app.modules.settings.usecases.get_public_appearance_image import (
    GetPublicAppearanceImageUseCase,
)


class GetPublicTabIconUseCase:
    """The favicon index.html links to: the cabinet falls back to the dashboard's."""

    def __init__(
        self, get_public_appearance_image_use_case: GetPublicAppearanceImageUseCase
    ) -> None:
        self._get_public_appearance_image_use_case = get_public_appearance_image_use_case

    async def execute(self, target: AppearanceTarget) -> Optional[AppearanceImageDto]:
        targets = [target]
        if target is AppearanceTarget.CABINET:
            targets.append(AppearanceTarget.DASHBOARD)
        for candidate in targets:
            try:
                return await self._get_public_appearance_image_use_case.execute(
                    candidate, AppearanceImageKind.FAVICON
                )
            except NotFoundError:
                continue
        return None
