from typing import Optional

import pytest

from app.core.constants import CUSTOM_DIALOG_WINDOW_SLOTS, DEFAULT_BANNER_NAME
from app.core.enums import ButtonLinkType, DialogFlow
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.dialogs.dto import (
    CreateCustomWindowDto,
    DialogButtonInputDto,
    UpdateDialogWindowDto,
)
from app.modules.dialogs.repository import DialogRepository
from app.modules.dialogs.seed import upsert_seed_window

_SNAPSHOT = {
    "banner_name": "menu",
    "buttons": [
        {
            "key": "sub",
            "text_key": "btn-menu.subscription",
            "row": 0,
            "column": 0,
            "link_type": "navigate",
            "action_key": None,
            "target_window_key": "subscription.main",
            "url_value": None,
            "visibility_condition": None,
            "style": None,
            "icon_custom_emoji_id": None,
            "is_hidden": False,
        }
    ],
}


async def test_upsert_seed_creates_once(dialog_repository: DialogRepository) -> None:
    created = await upsert_seed_window(
        dialog_repository,
        "menu.main",
        DialogFlow.MENU,
        "menu",
        _SNAPSHOT,
    )
    assert created is True
    again = await upsert_seed_window(
        dialog_repository,
        "menu.main",
        DialogFlow.MENU,
        "menu",
        _SNAPSHOT,
    )
    assert again is False

    window = await dialog_repository.get_by_key_or_raise("menu.main")
    assert window.banner_name == "menu"
    assert len(window.buttons) == 1
    assert window.buttons[0].link_type == ButtonLinkType.NAVIGATE


async def test_replace_buttons_swaps_layout(dialog_repository: DialogRepository) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    updated = await dialog_repository.replace_buttons(
        "menu.main",
        [
            DialogButtonInputDto(
                row=0,
                column=0,
                text_key="btn-menu.trial",
                link_type=ButtonLinkType.ACTION,
                action_key="get_trial",
            ),
            DialogButtonInputDto(
                row=1,
                column=0,
                text_key="btn-menu.help",
                link_type=ButtonLinkType.URL,
                url_value="https://help",
            ),
        ],
    )
    assert [b.text_key for b in updated.buttons] == ["btn-menu.trial", "btn-menu.help"]


async def test_reset_restores_snapshot(dialog_repository: DialogRepository) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    await dialog_repository.update_window(
        "menu.main",
        UpdateDialogWindowDto(banner_name=DEFAULT_BANNER_NAME, banner_name_provided=True),
    )
    await dialog_repository.replace_buttons("menu.main", [])

    reset = await dialog_repository.reset_to_snapshot("menu.main")
    assert reset.banner_name == "menu"
    assert [b.text_key for b in reset.buttons] == ["btn-menu.subscription"]


async def test_reseed_refreshes_snapshot_but_keeps_edited_buttons(
    dialog_repository: DialogRepository,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    await dialog_repository.replace_buttons(
        "menu.main",
        [
            DialogButtonInputDto(
                row=0,
                column=0,
                text_key="btn-menu.help",
                link_type=ButtonLinkType.URL,
                url_value="https://help",
            )
        ],
    )
    trial = {
        "key": "trial",
        "text_key": "btn-menu.trial",
        "row": 1,
        "column": 0,
        "link_type": "action",
        "action_key": "get_trial",
    }
    changed = {"banner_name": "menu", "buttons": [_SNAPSHOT["buttons"][0], trial]}

    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", changed)

    window = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [b.text_key for b in window.buttons] == ["btn-menu.help"]
    assert await dialog_repository.seed_action_keys("menu.main") == {"get_trial"}
    reset = await dialog_repository.reset_to_snapshot("menu.main")
    assert [b.text_key for b in reset.buttons] == ["btn-menu.subscription", "btn-menu.trial"]


async def test_reseed_moves_untouched_buttons_to_the_new_layout(
    dialog_repository: DialogRepository,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    moved = {**_SNAPSHOT["buttons"][0], "row": 2, "column": 1}
    trial = {
        "key": "trial",
        "text_key": "btn-menu.trial",
        "row": 0,
        "column": 0,
        "link_type": "action",
        "action_key": "get_trial",
    }

    await upsert_seed_window(
        dialog_repository,
        "menu.main",
        DialogFlow.MENU,
        "menu",
        {"banner_name": "menu", "buttons": [moved, trial]},
    )

    window = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [(b.text_key, b.row, b.column) for b in window.buttons] == [
        ("btn-menu.trial", 0, 0),
        ("btn-menu.subscription", 2, 1),
    ]


async def test_update_window_clears_banner_only_when_told_to(
    dialog_repository: DialogRepository,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)

    untouched = await dialog_repository.update_window(
        "menu.main", UpdateDialogWindowDto(is_enabled=False)
    )
    assert untouched.banner_name == "menu"

    cleared = await dialog_repository.update_window(
        "menu.main", UpdateDialogWindowDto(banner_name=None, banner_name_provided=True)
    )
    assert cleared.banner_name is None


async def test_custom_windows_take_free_slots_until_the_pool_runs_out(
    dialog_repository: DialogRepository,
) -> None:
    for index in range(CUSTOM_DIALOG_WINDOW_SLOTS):
        window = await dialog_repository.create_custom_window(
            f"custom.{index}", CreateCustomWindowDto()
        )
        assert window.slot == index

    with pytest.raises(ValidationError):
        await dialog_repository.create_custom_window("custom.overflow", CreateCustomWindowDto())


async def test_create_custom_window_conflict(dialog_repository: DialogRepository) -> None:
    await dialog_repository.create_custom_window("custom.faq", CreateCustomWindowDto())
    with pytest.raises(ConflictError):
        await dialog_repository.create_custom_window("custom.faq", CreateCustomWindowDto())


async def test_get_by_key_or_raise_missing(dialog_repository: DialogRepository) -> None:
    with pytest.raises(NotFoundError):
        await dialog_repository.get_by_key_or_raise("nope")


async def _move_with_condition(
    dialog_repository: DialogRepository, condition: Optional[str]
) -> None:
    # moved, so the layout counts as edited
    await dialog_repository.replace_buttons(
        "menu.main",
        [
            DialogButtonInputDto(
                row=2,
                column=0,
                text_key="btn-menu.subscription",
                link_type=ButtonLinkType.NAVIGATE,
                target_window_key="subscription.main",
                visibility_condition=condition,
            )
        ],
    )


async def _reseed_with_condition(dialog_repository: DialogRepository, condition: str) -> None:
    [button] = _SNAPSHOT["buttons"]
    gated = {**_SNAPSHOT, "buttons": [{**button, "visibility_condition": condition}]}
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", gated)


async def test_reseed_moves_a_shipped_condition_to_the_catalog_one(
    dialog_repository: DialogRepository,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    await _move_with_condition(dialog_repository, None)

    await _reseed_with_condition(dialog_repository, "has_subscriptions")

    window = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [(b.row, b.visibility_condition) for b in window.buttons] == [(2, "has_subscriptions")]


async def test_reseed_keeps_a_condition_the_admin_chose(
    dialog_repository: DialogRepository,
) -> None:
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    await _move_with_condition(dialog_repository, "trial_available")

    await _reseed_with_condition(dialog_repository, "has_subscriptions")

    window = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [(b.row, b.visibility_condition) for b in window.buttons] == [(2, "trial_available")]


async def test_required_comes_from_the_catalog_snapshot(
    dialog_repository: DialogRepository,
) -> None:
    [button] = _SNAPSHOT["buttons"]
    snapshot = {**_SNAPSHOT, "buttons": [{**button, "required": True}]}
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", snapshot)

    window = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [(b.is_system, b.is_required) for b in window.buttons] == [(True, True)]
    assert await dialog_repository.required_conditions("menu.main") == {
        "btn-menu.subscription": None
    }


async def test_reseed_keeps_button_ids_while_the_layout_stays(
    dialog_repository: DialogRepository,
) -> None:
    # an id lives in the callback data of messages already sent
    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", _SNAPSHOT)
    before = await dialog_repository.get_by_key_or_raise("menu.main")
    [button] = _SNAPSHOT["buttons"]
    flagged = {**_SNAPSHOT, "buttons": [{**button, "required": True}]}

    await upsert_seed_window(dialog_repository, "menu.main", DialogFlow.MENU, "menu", flagged)

    after = await dialog_repository.get_by_key_or_raise("menu.main")
    assert [b.id for b in after.buttons] == [b.id for b in before.buttons]
    assert [b.is_required for b in after.buttons] == [True]
