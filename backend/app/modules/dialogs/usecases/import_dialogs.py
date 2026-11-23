from dataclasses import replace

from app.core.actor import Actor
from app.core.config import AppConfig
from app.core.db.uow import UnitOfWork
from app.core.enums import DialogWindowKind, Locale
from app.core.error_codes import ErrorCode
from app.core.exceptions import ValidationError
from app.core.ports import BotSignals, EventPublisher
from app.core.security.permissions import Permission
from app.core.usecase import UseCase
from app.modules.banners import BannerRepository
from app.modules.dialogs import catalog
from app.modules.dialogs.dto import (
    CreateCustomWindowDto,
    DialogExportDto,
    DialogWindowExportDto,
    UpdateDialogWindowDto,
)
from app.modules.dialogs.events import DialogChangedEvent
from app.modules.dialogs.export import (
    EXPORT_VERSION,
    LOCKED_CONDITIONS_EXPORT_VERSION,
    dialog_text_keys,
)
from app.modules.dialogs.repository import DialogRepository
from app.modules.dialogs.usecases._banners import drop_window_banners
from app.modules.dialogs.usecases._window_texts import validate_stored_window_texts
from app.modules.dialogs.validation import validate_buttons
from app.modules.translations import (
    TranslationRepository,
    assert_fluent_compiles,
    find_disallowed_placeable,
    join_message_key,
    serialize_to_fluent,
)


class ImportDialogsUseCase(UseCase[DialogExportDto, None]):
    """Replaces the whole dialog configuration with the imported one: seed
    windows are overwritten, custom windows are brought in line with the file,
    and custom windows missing from it are deleted."""

    required_permission = Permission.DIALOGS_IMPORT

    def __init__(
        self,
        dialog_repository: DialogRepository,
        translation_repository: TranslationRepository,
        banner_repository: BannerRepository,
        config: AppConfig,
        uow: UnitOfWork,
        bot_signals: BotSignals,
        event_publisher: EventPublisher,
    ) -> None:
        self._dialog_repository = dialog_repository
        self._translation_repository = translation_repository
        self._banner_repository = banner_repository
        self._config = config
        self._uow = uow
        self._bot_signals = bot_signals
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: DialogExportDto) -> None:
        if data.version not in (EXPORT_VERSION, LOCKED_CONDITIONS_EXPORT_VERSION):
            raise ValidationError(
                f"Unsupported export version {data.version}, expected {EXPORT_VERSION}",
                code=ErrorCode.DIALOG_EXPORT_VERSION_UNSUPPORTED,
            )

        async with self._uow:
            existing = await self._dialog_repository.list_all()
            imported_keys = {window.key for window in data.windows}

            for current in existing:
                if current.kind == DialogWindowKind.CUSTOM and current.key not in imported_keys:
                    await self._dialog_repository.delete_custom_window(current.key)
                    await drop_window_banners(
                        self._banner_repository, self._config, self._uow, current.key
                    )

            known_keys = {
                current.key
                for current in existing
                if current.kind != DialogWindowKind.CUSTOM or current.key in imported_keys
            }
            navigable = imported_keys | {window.key for window in catalog.all_windows()}
            for imported in data.windows:
                await self._apply_window(
                    imported,
                    known_keys,
                    navigable,
                    catalog_conditions=data.version == LOCKED_CONDITIONS_EXPORT_VERSION,
                )

            windows = await self._dialog_repository.list_all()
            await self._apply_texts(data, dialog_text_keys(windows))
            for window in windows:
                await validate_stored_window_texts(
                    window.key,
                    window.banner_name,
                    self._translation_repository,
                )
            await self._event_publisher.publish(DialogChangedEvent(actor=actor, action="import"))

        await self._bot_signals.dialogs_changed()

    async def _apply_window(
        self,
        window: DialogWindowExportDto,
        known_keys: set[str],
        navigable: set[str],
        *,
        catalog_conditions: bool,
    ) -> None:
        if window.key not in known_keys:
            if window.kind == DialogWindowKind.SEED:
                # Seed windows come from the in-process catalog, not from a file:
                # one this build doesn't know about has no flow, no snapshot and
                # no state to render through.
                return
            await self._dialog_repository.create_custom_window(
                window.key,
                CreateCustomWindowDto(
                    banner_name=window.banner_name, name=window.name, position=window.position
                ),
            )

        seed_actions = (
            await self._dialog_repository.seed_action_keys(window.key)
            if window.kind == DialogWindowKind.SEED
            else set()
        )
        buttons = window.buttons
        if catalog_conditions:
            system_conditions = await self._dialog_repository.system_conditions(window.key)
            buttons = [
                replace(button, visibility_condition=system_conditions[button.text_key])
                if button.text_key in system_conditions
                else button
                for button in buttons
            ]
        validate_buttons(
            buttons,
            window_key=window.key,
            window_kind=window.kind,
            navigable=navigable,
            seed_actions=seed_actions,
            required_conditions=await self._dialog_repository.required_conditions(window.key),
        )
        await self._dialog_repository.update_window(
            window.key,
            UpdateDialogWindowDto(
                banner_name=window.banner_name,
                banner_name_provided=True,
                # seed windows are always on, whatever an older export says
                is_enabled=True if window.kind == DialogWindowKind.SEED else window.is_enabled,
                name=window.name if window.kind == DialogWindowKind.CUSTOM else None,
            ),
        )
        await self._dialog_repository.replace_buttons(window.key, buttons)

    async def _apply_texts(self, data: DialogExportDto, text_keys: set[str]) -> None:
        active = set(await self._translation_repository.list_active_locales())
        touched: set[Locale] = set()

        for text in data.texts:
            if text.locale not in active:
                continue
            if "." in text.message_id:
                raise ValidationError(
                    f"'{text.message_id}' is a message id plus an attribute -- "
                    "pass the attribute as attribute_id",
                    code=ErrorCode.DIALOG_TEXT_KEY_HAS_ATTRIBUTE,
                )
            full_key = join_message_key(text.message_id, text.attribute_id)
            if full_key not in text_keys:
                # Import must not become a back door into translations that have
                # nothing to do with dialogs, such as bot command descriptions.
                continue
            window_key = catalog.window_key_for_text_key(full_key)
            disallowed = find_disallowed_placeable(
                text.value, catalog.allowed_text_variables(window_key)
            )
            if disallowed is not None:
                raise ValidationError(disallowed, code=ErrorCode.DIALOG_TEXT_VARIABLE_NOT_ALLOWED)
            # an icon's default stays empty, so a reset takes the icon off
            default = "" if catalog.is_icon_text_key(full_key) else text.value
            await self._translation_repository.seed_missing(
                text.locale, text.message_id, text.attribute_id, default
            )
            await self._translation_repository.update_value(
                text.locale, text.message_id, text.attribute_id, text.value
            )
            touched.add(text.locale)

        for locale in touched:
            entries = await self._translation_repository.list_by_locale(locale)
            assert_fluent_compiles(locale, serialize_to_fluent(entries))
