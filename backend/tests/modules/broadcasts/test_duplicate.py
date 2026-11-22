from pathlib import Path

import pytest

from app.core.config import AppConfig
from app.core.db.uow import UnitOfWork
from app.core.enums import BroadcastDelivery, BroadcastMediaType, BroadcastStatus, Locale, Role
from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.core.ports import EventPublisher
from app.modules.broadcasts.dto import (
    BroadcastEmailContentDto,
    BroadcastMediaDto,
    ListBroadcastsDto,
)
from app.modules.broadcasts.repository import BroadcastRepository
from app.modules.broadcasts.storage import email_image_path, media_path
from app.modules.broadcasts.usecases import DuplicateBroadcastUseCase
from app.modules.users.dto import CreateUserDto, UserDto
from app.modules.users.repository import UserRepository
from tests.modules.broadcasts.factories import set_broadcast
from tests.modules.broadcasts.test_usecases import _make


@pytest.fixture
async def admin(user_repository: UserRepository) -> UserDto:
    return await user_repository.create(
        CreateUserDto(name="admin", role=Role.ADMIN, login="dup-admin")
    )


@pytest.fixture
def media_config(config: AppConfig, tmp_path: Path) -> AppConfig:
    return config.model_copy(update={"media_dir": tmp_path})


@pytest.fixture
def duplicate_use_case(
    broadcast_repository: BroadcastRepository,
    media_config: AppConfig,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> DuplicateBroadcastUseCase:
    return DuplicateBroadcastUseCase(broadcast_repository, media_config, uow, event_publisher)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def test_duplicate_of_sent_broadcast_is_a_fresh_draft(
    duplicate_use_case: DuplicateBroadcastUseCase,
    broadcast_repository: BroadcastRepository,
    admin: UserDto,
) -> None:
    source = await _make(broadcast_repository, BroadcastStatus.COMPLETED, title="Promo")
    await set_broadcast(
        broadcast_repository.session,
        source.id,
        sent_count=5,
        channel_chat_id=1,
        channel_message_id=2,
        media_file_id="tg-file",
    )

    copy = await duplicate_use_case(admin, source.id)

    assert copy.id != source.id
    assert copy.status is BroadcastStatus.DRAFT
    assert copy.title == "Promo"
    assert copy.contents == source.contents
    assert copy.created_by == admin.id
    assert (copy.sent_count, copy.channel_message_id, copy.media_file_id) == (0, None, None)
    assert (await broadcast_repository.get_by_id(source.id)).status is BroadcastStatus.COMPLETED


async def test_duplicate_copies_media_and_email_images(
    duplicate_use_case: DuplicateBroadcastUseCase,
    broadcast_repository: BroadcastRepository,
    media_config: AppConfig,
    admin: UserDto,
) -> None:
    media_dir = media_config.media_dir
    source = await _make(
        broadcast_repository,
        delivery=BroadcastDelivery.ALL,
        email_contents={Locale.EN: BroadcastEmailContentDto(subject="Hi", html="<p>x</p>")},
    )
    await broadcast_repository.set_media(
        source.id,
        BroadcastMediaDto(
            media_type=BroadcastMediaType.PHOTO,
            content_type="image/png",
            extension="png",
            version=3,
            size_bytes=5,
        ),
    )
    _write(media_path(media_dir, source.id, 3, "png"), b"photo")
    image = await broadcast_repository.add_email_image(source.id, "image/png", "png", 3)
    _write(email_image_path(media_dir, source.id, image.id, "png"), b"img")
    html = f'<p><img src="bc-image:{image.id}" alt=""></p>'
    await broadcast_repository.set_email_contents(
        source.id, {Locale.EN: BroadcastEmailContentDto(subject="Hi", html=html)}
    )

    copy = await duplicate_use_case(admin, source.id)

    assert media_path(media_dir, copy.id, 3, "png").read_bytes() == b"photo"
    [copied] = await broadcast_repository.list_email_images(copy.id)
    assert email_image_path(media_dir, copy.id, copied.id, "png").read_bytes() == b"img"
    assert copy.email_contents[Locale.EN].html == f'<p><img src="bc-image:{copied.id}" alt=""></p>'
    # the source keeps its own files and references
    assert media_path(media_dir, source.id, 3, "png").exists()
    assert (await broadcast_repository.get_by_id(source.id)).email_contents[Locale.EN].html == html


async def test_duplicate_skips_a_file_missing_on_disk(
    duplicate_use_case: DuplicateBroadcastUseCase,
    broadcast_repository: BroadcastRepository,
    media_config: AppConfig,
    admin: UserDto,
) -> None:
    source = await _make(broadcast_repository)
    await broadcast_repository.set_media(
        source.id,
        BroadcastMediaDto(
            media_type=BroadcastMediaType.PHOTO,
            content_type="image/png",
            extension="png",
            version=1,
            size_bytes=5,
        ),
    )

    copy = await duplicate_use_case(admin, source.id)

    assert copy.status is BroadcastStatus.DRAFT
    assert not media_path(media_config.media_dir, copy.id, 1, "png").exists()


async def test_duplicate_whose_file_copy_fails_leaves_nothing(
    duplicate_use_case: DuplicateBroadcastUseCase,
    broadcast_repository: BroadcastRepository,
    media_config: AppConfig,
    admin: UserDto,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_dir = media_config.media_dir
    source = await _make(broadcast_repository)
    image = await broadcast_repository.add_email_image(source.id, "image/png", "png", 3)
    _write(email_image_path(media_dir, source.id, image.id, "png"), b"img")

    def _disk_full(*_: object) -> None:
        raise OSError("No space left on device")

    monkeypatch.setattr("shutil.copyfile", _disk_full)
    with pytest.raises(OSError):
        await duplicate_use_case(admin, source.id)

    items, _ = await broadcast_repository.list(ListBroadcastsDto())
    assert [b.id for b in items] == [source.id]
    assert [p.name for p in (media_dir / "broadcasts" / "email").iterdir()] == [str(source.id)]


async def test_duplicate_while_sending(
    duplicate_use_case: DuplicateBroadcastUseCase,
    broadcast_repository: BroadcastRepository,
    admin: UserDto,
) -> None:
    source = await _make(broadcast_repository, BroadcastStatus.SENDING)
    assert (await duplicate_use_case(admin, source.id)).status is BroadcastStatus.DRAFT


async def test_duplicate_of_missing_broadcast(
    duplicate_use_case: DuplicateBroadcastUseCase, admin: UserDto
) -> None:
    with pytest.raises(NotFoundError):
        await duplicate_use_case(admin, 999_999)


async def test_duplicate_requires_permission(
    duplicate_use_case: DuplicateBroadcastUseCase, broadcast_repository: BroadcastRepository
) -> None:
    source = await _make(broadcast_repository)
    user = UserDto(id=77, name="u", role=Role.USER, language=Locale.EN)
    with pytest.raises(PermissionDeniedError):
        await duplicate_use_case(user, source.id)
