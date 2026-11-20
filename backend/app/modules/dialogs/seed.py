from typing import Optional

from app.core.enums import DialogFlow, DialogWindowKind
from app.modules.dialogs import catalog
from app.modules.dialogs.repository import DialogRepository
from app.modules.dialogs.snapshot import buttons_layout, snapshot_conditions, snapshot_layout
from app.modules.translations import TranslationRepository, split_message_key


async def upsert_seed_window(
    dialog_repository: DialogRepository,
    key: str,
    flow: DialogFlow,
    banner_name: Optional[str],
    default_snapshot: dict,
    position: int = 0,
) -> bool:
    """Returns whether the window was created."""
    existing = await dialog_repository.find_by_key(key)
    if existing is None:
        await dialog_repository.create_seed(key, flow, banner_name, default_snapshot, position)
        return True

    # The catalog owns a seed window's place in its flow and its default layout;
    # buttons the admin edited stay, untouched ones follow the new layout.
    shipped = await dialog_repository.get_default_snapshot(key)
    shipped_layout = snapshot_layout(shipped)
    untouched = buttons_layout(existing.buttons) == shipped_layout
    # recreated buttons get new ids, which kills them in messages already sent
    replace_buttons = untouched and shipped_layout != snapshot_layout(default_snapshot)

    # a condition the admin left as shipped follows the catalog; an edited one stays
    conditions: dict[int, Optional[str]] = {}
    if existing.kind == DialogWindowKind.SEED and not replace_buttons:
        previous = snapshot_conditions(shipped)
        current = snapshot_conditions(default_snapshot)
        conditions = {
            button.id: current[button.text_key]
            for button in existing.buttons
            if button.text_key in current
            and button.text_key in previous
            and button.visibility_condition == previous[button.text_key]
        }

    await dialog_repository.update_seed(
        key,
        position=position,
        default_snapshot=default_snapshot,
        replace_buttons=replace_buttons,
        conditions=conditions,
    )
    return False


async def ensure_dialogs_seeded(
    dialog_repository: DialogRepository,
    translation_repository: TranslationRepository,
) -> None:
    locales = await translation_repository.list_active_locales()
    for position, window in enumerate(catalog.all_windows()):
        await upsert_seed_window(
            dialog_repository,
            window.key,
            window.flow,
            window.banner_name,
            catalog.window_snapshot(window.key),
            position=position,
        )
        text_keys = sorted(set(window.text_keys) | {b.text_key for b in window.buttons})
        rows = [(*split_message_key(text_key), "") for text_key in text_keys]
        # Earlier seeds stored a dotted key as a message id of its own, which
        # shadows the real row everywhere the two are looked up by joined key.
        dotted = [
            text_key
            for text_key, (_, attribute_id, _) in zip(text_keys, rows, strict=True)
            if attribute_id
        ]
        for locale in locales:
            await translation_repository.seed_missing_many(locale, rows)
            await translation_repository.delete_blank_messages(locale, dotted)
