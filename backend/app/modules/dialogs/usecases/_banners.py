from loguru import logger

from app.core.config import AppConfig
from app.core.constants import WINDOW_BANNER_PREFIX
from app.core.db.uow import UnitOfWork
from app.modules.banners import BannerRepository, delete_banner_file


async def drop_window_banners(
    banner_repository: BannerRepository, config: AppConfig, uow: UnitOfWork, window_key: str
) -> None:
    """A window's own banner is named after its key and nothing else points at
    it, so it dies with the window instead of lingering in the media dir."""
    orphaned = await banner_repository.delete_all_named(f"{WINDOW_BANNER_PREFIX}{window_key}")

    # files go only once the rows are gone for good; a rollback keeps both
    async def delete_files() -> None:
        for banner in orphaned:
            try:
                delete_banner_file(config.media_dir, banner.name, banner.locale, banner.extension)
            except OSError as error:
                # the rows are already gone; a leftover file must not fail the request
                logger.warning(f"Failed to delete banner file '{banner.name}': {error}")

    await uow.after_commit(delete_files)
