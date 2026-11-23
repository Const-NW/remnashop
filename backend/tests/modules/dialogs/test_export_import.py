from dataclasses import replace

import pytest
from sqlalchemy import delete

from app.core.bot_signals import RedisBotSignals
from app.core.constants import WINDOW_BANNER_PREFIX
from app.core.db.models import BotTranslation
from app.core.enums import ButtonLinkType, DialogFlow, DialogWindowKind, Locale, Role
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.modules.banners.dto import UploadBannerDto
from app.modules.banners.storage import banner_path, save_banner_file
from app.modules.dialogs import catalog
from app.modules.dialogs.dto import (
    CreateCustomWindowDto,
    DialogButtonInputDto,
    DialogExportDto,
    DialogTextExportDto,
    DialogWindowExportDto,
    UpdateDialogWindowDto,
)
from app.modules.dialogs.export import EXPORT_VERSION, LOCKED_CONDITIONS_EXPORT_VERSION
from app.modules.dialogs.repository import DialogRepository
from app.modules.dialogs.seed import upsert_seed_window
from app.modules.dialogs.usecases import ExportDialogsUseCase, ImportDialogsUseCase
from app.modules.users.dto import UserDto
from tests.fakes.events import RecordingEventPublisher

OWNER = UserDto(id=1, login="a", name="a", role=Role.OWNER, language=Locale.EN)

_SNAPSHOT = {
    "banner_name": "menu",
    "buttons": [
        {
            "key": "trial",
            "text_key": "btn-menu.trial",
            "row": 0,
            "column": 0,
            "link_type": "action",
            "action_key": "get_trial",
            "target_window_key": None,
            "url_value": None,
            "visibility_condition": None,
            "style": None,
            "icon_custom_emoji_id": None,
            "is_hidden": False,
        }
    ],
}


@pytest.fixture(autouse=True)
def _catalog():
    catalog._reset_for_tests()
    catalog.register_action_key("get_trial")
    catalog.register_condition("trial_available", lambda data: True)
    catalog.register_window(
        catalog.CatalogWindow(
            key="menu.main",
            flow=DialogFlow.MENU,
            text_keys=("msg-main-menu",),
            banner_name="menu",
            buttons=(
                catalog.CatalogButton(
                    key="trial",
                    text_key="btn-menu.trial",
                    link_type=ButtonLinkType.ACTION,
                    action_key="get_trial",
                ),
            ),
        )
    )
    yield
    catalog._reset_for_tests()


async def _export(dialog_repository, translation_repository, settings_repository, uow):
    return await ExportDialogsUseCase(dialog_repository, translation_repository, uow)(OWNER)


async def test_export_carries_windows_and_their_texts(
    dialog_repository: DialogRepository, translation_repository, settings_repository, uow
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    if Locale.EN not in await translation_repository.list_active_locales():
        await translation_repository.add_active_locale(Locale.EN)
    await translation_repository.seed_missing(Locale.EN, "btn-menu", "trial", "Try for free")
    # Not a dialog key: must stay out of the file, since import writes back what it finds.
    await translation_repository.seed_missing(Locale.EN, "cmd-start", "", "Start")

    export = await _export(dialog_repository, translation_repository, settings_repository, uow)

    assert export.version == EXPORT_VERSION
    assert [w.key for w in export.windows] == ["menu.main"]
    assert [b.text_key for b in export.windows[0].buttons] == ["btn-menu.trial"]
    assert ("btn-menu", "trial") in {(t.message_id, t.attribute_id) for t in export.texts}
    assert "cmd-start" not in {t.message_id for t in export.texts}


async def test_import_restores_an_exported_configuration(
    dialog_repository: DialogRepository,
    translation_repository,
    settings_repository,
    uow,
    redis,
    banner_repository,
    config,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    if Locale.EN not in await translation_repository.list_active_locales():
        await translation_repository.add_active_locale(Locale.EN)
    await translation_repository.seed_missing(Locale.EN, "btn-menu", "trial", "Try for free")

    export = await _export(dialog_repository, translation_repository, settings_repository, uow)

    # Drift away from the exported state, then import it back.
    await dialog_repository.replace_buttons("menu.main", [])
    await translation_repository.update_value(Locale.EN, "btn-menu", "trial", "Changed")

    await ImportDialogsUseCase(
        dialog_repository,
        translation_repository,
        banner_repository,
        config,
        uow,
        RedisBotSignals(redis),
        RecordingEventPublisher(),
    )(OWNER, export)

    restored = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [b.text_key for b in restored.buttons] == ["btn-menu.trial"]
    label = await translation_repository.get(Locale.EN, "btn-menu", "trial")
    assert label is not None
    assert label.value == "Try for free"


async def test_import_deletes_custom_windows_missing_from_the_file(
    dialog_repository: DialogRepository,
    translation_repository,
    settings_repository,
    uow,
    redis,
    banner_repository,
    config,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    export = await _export(dialog_repository, translation_repository, settings_repository, uow)
    await dialog_repository.create_custom_window("custom.later", CreateCustomWindowDto())

    await ImportDialogsUseCase(
        dialog_repository,
        translation_repository,
        banner_repository,
        config,
        uow,
        RedisBotSignals(redis),
        RecordingEventPublisher(),
    )(OWNER, export)

    assert await dialog_repository.find_by_key("custom.later") is None


async def test_import_creates_a_custom_window_from_the_file(
    dialog_repository: DialogRepository,
    translation_repository,
    settings_repository,
    uow,
    redis,
    banner_repository,
    config,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    if Locale.EN not in await translation_repository.list_active_locales():
        await translation_repository.add_active_locale(Locale.EN)

    payload = DialogExportDto(
        version=EXPORT_VERSION,
        windows=[
            DialogWindowExportDto(
                key="custom-faq",
                kind=DialogWindowKind.CUSTOM,
                banner_name=None,
                is_enabled=True,
                position=0,
                buttons=[
                    DialogButtonInputDto(
                        row=0,
                        column=0,
                        text_key="msg-dialog-custom-back",
                        link_type=ButtonLinkType.NAVIGATE,
                        target_window_key="menu.main",
                    )
                ],
            )
        ],
        texts=[
            DialogTextExportDto(
                locale=Locale.EN, message_id="msg-dialog-custom-faq", attribute_id="", value="FAQ"
            )
        ],
    )

    await ImportDialogsUseCase(
        dialog_repository,
        translation_repository,
        banner_repository,
        config,
        uow,
        RedisBotSignals(redis),
        RecordingEventPublisher(),
    )(OWNER, payload)

    created = await dialog_repository.find_by_key("custom-faq")
    assert created is not None
    assert created.slot is not None
    assert [b.target_window_key for b in created.buttons] == ["menu.main"]
    body = await translation_repository.get(Locale.EN, "msg-dialog-custom-faq", "")
    assert body is not None
    assert body.value == "FAQ"


async def test_import_rejects_a_button_pointing_nowhere(
    dialog_repository: DialogRepository,
    translation_repository,
    settings_repository,
    uow,
    redis,
    banner_repository,
    config,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    payload = DialogExportDto(
        version=EXPORT_VERSION,
        windows=[
            DialogWindowExportDto(
                key="menu.main",
                kind=DialogWindowKind.SEED,
                banner_name="menu",
                is_enabled=True,
                position=0,
                buttons=[
                    DialogButtonInputDto(
                        row=0,
                        column=0,
                        text_key="btn-menu.trial",
                        link_type=ButtonLinkType.NAVIGATE,
                        target_window_key="menu.nowhere",
                    )
                ],
            )
        ],
        texts=[],
    )

    with pytest.raises(ValidationError):
        await ImportDialogsUseCase(
            dialog_repository,
            translation_repository,
            banner_repository,
            config,
            uow,
            RedisBotSignals(redis),
            RecordingEventPublisher(),
        )(OWNER, payload)


async def test_import_rejects_an_unknown_version(
    dialog_repository: DialogRepository,
    translation_repository,
    settings_repository,
    uow,
    redis,
    banner_repository,
    config,
) -> None:
    with pytest.raises(ValidationError):
        await ImportDialogsUseCase(
            dialog_repository,
            translation_repository,
            banner_repository,
            config,
            uow,
            RedisBotSignals(redis),
            RecordingEventPublisher(),
        )(OWNER, DialogExportDto(version=99, windows=[], texts=[]))


@pytest.mark.parametrize(
    ("version", "expected"),
    [(EXPORT_VERSION, "trial_available"), (LOCKED_CONDITIONS_EXPORT_VERSION, None)],
)
async def test_import_takes_system_button_conditions_from_the_file_only_once_editable(
    dialog_repository: DialogRepository,
    translation_repository,
    settings_repository,
    uow,
    redis,
    version: int,
    expected: object,
    banner_repository,
    config,
) -> None:
    # before conditions were editable, an export carried whatever the catalog said then
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    export = await _export(dialog_repository, translation_repository, settings_repository, uow)
    [window] = export.windows
    [button] = window.buttons
    edited = replace(
        export,
        version=version,
        windows=[
            replace(window, buttons=[replace(button, visibility_condition="trial_available")])
        ],
    )

    await ImportDialogsUseCase(
        dialog_repository,
        translation_repository,
        banner_repository,
        config,
        uow,
        RedisBotSignals(redis),
        RecordingEventPublisher(),
    )(OWNER, edited)

    restored = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [b.visibility_condition for b in restored.buttons] == [expected]


def _importer(dialog_repository, translation_repository, banner_repository, config, uow, redis):
    return ImportDialogsUseCase(
        dialog_repository,
        translation_repository,
        banner_repository,
        config,
        uow,
        RedisBotSignals(redis),
        RecordingEventPublisher(),
    )


async def _seed_menu(dialog_repository, translation_repository) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    if Locale.EN not in await translation_repository.list_active_locales():
        await translation_repository.add_active_locale(Locale.EN)
    await translation_repository.seed_missing(Locale.EN, "btn-menu", "trial", "Try for free")


async def test_button_icons_survive_export_and_import(
    dialog_repository,
    translation_repository,
    settings_repository,
    banner_repository,
    config,
    uow,
    redis,
) -> None:
    await _seed_menu(dialog_repository, translation_repository)
    for attribute_id, value in (("trial-icon", "🎁"), ("trial-icon-id", "5368324170671202286")):
        await translation_repository.seed_missing(Locale.EN, "btn-menu", attribute_id, "")
        await translation_repository.update_value(Locale.EN, "btn-menu", attribute_id, value)

    export = await _export(dialog_repository, translation_repository, settings_repository, uow)
    # a fresh install has no icon rows until something sets them
    await translation_repository.session.execute(
        delete(BotTranslation).where(BotTranslation.attribute_id.like("trial-icon%"))
    )
    await _importer(
        dialog_repository, translation_repository, banner_repository, config, uow, redis
    )(OWNER, export)

    icon = await translation_repository.get(Locale.EN, "btn-menu", "trial-icon")
    icon_id = await translation_repository.get(Locale.EN, "btn-menu", "trial-icon-id")
    assert icon is not None and (icon.value, icon.default_value) == ("🎁", "")
    assert icon_id is not None
    assert (icon_id.value, icon_id.default_value) == ("5368324170671202286", "")


async def test_import_refuses_a_variable_the_window_does_not_have(
    dialog_repository,
    translation_repository,
    settings_repository,
    banner_repository,
    config,
    uow,
    redis,
) -> None:
    await _seed_menu(dialog_repository, translation_repository)
    await translation_repository.seed_missing(Locale.EN, "msg-main-menu", "", "Hi")
    export = await _export(dialog_repository, translation_repository, settings_repository, uow)
    edited = replace(
        export,
        texts=[
            DialogTextExportDto(
                locale=Locale.EN,
                message_id="msg-main-menu",
                attribute_id="",
                value="Hi { $not_a_variable }",
            )
        ],
    )

    with pytest.raises(ValidationError) as error:
        await _importer(
            dialog_repository, translation_repository, banner_repository, config, uow, redis
        )(OWNER, edited)
    assert error.value.code is ErrorCode.DIALOG_TEXT_VARIABLE_NOT_ALLOWED


async def test_import_restores_a_custom_window_name(
    dialog_repository,
    translation_repository,
    settings_repository,
    banner_repository,
    config,
    uow,
    redis,
) -> None:
    await _seed_menu(dialog_repository, translation_repository)
    await dialog_repository.create_custom_window("custom-faq", CreateCustomWindowDto(name="FAQ"))
    export = await _export(dialog_repository, translation_repository, settings_repository, uow)
    await dialog_repository.update_window("custom-faq", UpdateDialogWindowDto(name="Renamed"))

    await _importer(
        dialog_repository, translation_repository, banner_repository, config, uow, redis
    )(OWNER, export)

    window = await dialog_repository.get_by_key_or_raise("custom-faq")
    assert window.name == "FAQ"


async def _custom_window_with_banner(dialog_repository, banner_repository, config):
    await dialog_repository.create_custom_window("custom-old", CreateCustomWindowDto())
    name = f"{WINDOW_BANNER_PREFIX}custom-old"
    await banner_repository.upsert(
        UploadBannerDto(
            name=name, locale=Locale.EN, content_type="image/png", extension="png", size_bytes=1
        )
    )
    save_banner_file(config.media_dir, name, Locale.EN, "png", b"x")
    return banner_path(config.media_dir, name, Locale.EN, "png")


async def test_import_drops_the_banners_of_a_window_it_deletes(
    dialog_repository,
    translation_repository,
    settings_repository,
    banner_repository,
    config,
    uow,
    redis,
    tmp_path,
) -> None:
    config = config.model_copy(update={"media_dir": tmp_path})
    await _seed_menu(dialog_repository, translation_repository)
    export = await _export(dialog_repository, translation_repository, settings_repository, uow)
    path = await _custom_window_with_banner(dialog_repository, banner_repository, config)

    await _importer(
        dialog_repository, translation_repository, banner_repository, config, uow, redis
    )(OWNER, export)

    assert await banner_repository.delete_all_named(f"{WINDOW_BANNER_PREFIX}custom-old") == []
    assert not path.exists()


async def test_a_refused_import_keeps_the_banner_files(
    dialog_repository,
    translation_repository,
    settings_repository,
    banner_repository,
    config,
    uow,
    redis,
    tmp_path,
) -> None:
    config = config.model_copy(update={"media_dir": tmp_path})
    await _seed_menu(dialog_repository, translation_repository)
    export = await _export(dialog_repository, translation_repository, settings_repository, uow)
    path = await _custom_window_with_banner(dialog_repository, banner_repository, config)
    [window] = export.windows
    [button] = window.buttons
    broken = replace(
        export,
        windows=[
            replace(
                window,
                buttons=[
                    replace(
                        button,
                        link_type=ButtonLinkType.NAVIGATE,
                        action_key=None,
                        target_window_key="menu.nowhere",
                    )
                ],
            )
        ],
    )

    with pytest.raises(ValidationError):
        await _importer(
            dialog_repository, translation_repository, banner_repository, config, uow, redis
        )(OWNER, broken)

    assert path.exists()
