from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CUSTOM_DIALOG_WINDOW_SLOTS
from app.core.db.models import DialogButton, DialogWindow
from app.core.enums import ButtonLinkType, ButtonStyleKind, DialogFlow, DialogWindowKind
from app.core.error_codes import ErrorCode
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.dialogs.dto import (
    CreateCustomWindowDto,
    DialogButtonDto,
    DialogButtonInputDto,
    DialogWindowDto,
    UpdateDialogWindowDto,
)
from app.modules.dialogs.snapshot import snapshot_conditions, snapshot_required_keys


def _optional_enum(value: Optional[str], enum: type) -> object:
    return enum(value) if value is not None else None


class DialogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _to_button_dto(
        self, row: DialogButton, system_text_keys: set[str], required_text_keys: set[str]
    ) -> DialogButtonDto:
        dto = DialogButtonDto.model_validate(row, from_attributes=True)
        dto.is_system = row.text_key in system_text_keys
        dto.is_required = row.text_key in required_text_keys
        return dto

    def _to_dto(self, row: DialogWindow) -> DialogWindowDto:
        system_text_keys = _system_text_keys(row)
        required_text_keys = _required_text_keys(row)
        return DialogWindowDto(
            id=row.id,
            key=row.key,
            kind=row.kind,
            flow=row.flow,
            banner_name=row.banner_name,
            name=row.name,
            is_enabled=row.is_enabled,
            position=row.position,
            slot=row.slot,
            buttons=[
                self._to_button_dto(b, system_text_keys, required_text_keys) for b in row.buttons
            ],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def _get_row(self, key: str) -> Optional[DialogWindow]:
        row: Optional[DialogWindow] = await self.session.scalar(
            select(DialogWindow).where(DialogWindow.key == key)
        )
        return row

    async def _get_row_or_raise(self, key: str) -> DialogWindow:
        row = await self._get_row(key)
        if row is None:
            raise NotFoundError(
                f"Dialog window '{key}' not found", code=ErrorCode.DIALOG_WINDOW_NOT_FOUND
            )
        return row

    async def list_all(self) -> list[DialogWindowDto]:
        result = await self.session.scalars(
            select(DialogWindow).order_by(
                DialogWindow.flow, DialogWindow.position, DialogWindow.key
            )
        )
        return [self._to_dto(row) for row in result]

    async def find_by_key(self, key: str) -> Optional[DialogWindowDto]:
        row = await self._get_row(key)
        return self._to_dto(row) if row is not None else None

    async def get_by_key_or_raise(self, key: str) -> DialogWindowDto:
        return self._to_dto(await self._get_row_or_raise(key))

    async def list_seed_keys(self) -> set[str]:
        result = await self.session.scalars(
            select(DialogWindow.key).where(DialogWindow.kind == DialogWindowKind.SEED)
        )
        return set(result)

    async def list_custom_keys(self) -> set[str]:
        result = await self.session.scalars(
            select(DialogWindow.key).where(DialogWindow.kind == DialogWindowKind.CUSTOM)
        )
        return set(result)

    def _button_from_snapshot(self, window_id: int, raw: dict) -> DialogButton:
        return DialogButton(
            window_id=window_id,
            row=raw["row"],
            column=raw["column"],
            text_key=raw["text_key"],
            link_type=ButtonLinkType(raw["link_type"]),
            action_key=raw.get("action_key"),
            target_window_key=raw.get("target_window_key"),
            url_value=raw.get("url_value"),
            visibility_condition=raw.get("visibility_condition"),
            style=_optional_enum(raw.get("style"), ButtonStyleKind),
            is_hidden=raw.get("is_hidden", False),
        )

    async def get_default_snapshot(self, key: str) -> dict:
        return (await self._get_row_or_raise(key)).default_snapshot

    async def create_seed(
        self,
        key: str,
        flow: DialogFlow,
        banner_name: Optional[str],
        default_snapshot: dict,
        position: int,
    ) -> None:
        window = DialogWindow(
            key=key,
            kind=DialogWindowKind.SEED,
            flow=flow,
            banner_name=banner_name,
            is_enabled=True,
            position=position,
            default_snapshot=default_snapshot,
        )
        self.session.add(window)
        await self.session.flush()
        for raw in default_snapshot.get("buttons", []):
            self.session.add(self._button_from_snapshot(window.id, raw))
        await self.session.flush()

    async def update_seed(
        self,
        key: str,
        *,
        position: int,
        default_snapshot: dict,
        replace_buttons: bool,
        conditions: dict[int, Optional[str]],
    ) -> None:
        """`conditions`: new visibility conditions by button id."""
        row = await self._get_row_or_raise(key)
        row.position = position
        row.default_snapshot = default_snapshot
        if replace_buttons:
            for button in list(row.buttons):
                await self.session.delete(button)
            await self.session.flush()
            for raw in default_snapshot.get("buttons", []):
                self.session.add(self._button_from_snapshot(row.id, raw))
        for button in row.buttons:
            if button.id in conditions:
                button.visibility_condition = conditions[button.id]
        await self.session.flush()
        await self.session.refresh(row)

    async def update_window(self, key: str, data: UpdateDialogWindowDto) -> DialogWindowDto:
        row = await self._get_row_or_raise(key)
        if data.banner_name_provided:
            row.banner_name = data.banner_name
        if data.is_enabled is not None:
            row.is_enabled = data.is_enabled
        if data.name is not None:
            row.name = data.name
        await self.session.flush()
        await self.session.refresh(row)
        return self._to_dto(row)

    async def replace_buttons(
        self, key: str, buttons: list[DialogButtonInputDto]
    ) -> DialogWindowDto:
        # two concurrent replaces must not interleave their delete/insert

        row = await self.session.scalar(
            select(DialogWindow).where(DialogWindow.key == key).with_for_update()
        )
        if row is None:
            raise NotFoundError(
                f"Dialog window '{key}' not found", code=ErrorCode.DIALOG_WINDOW_NOT_FOUND
            )
        for existing in list(row.buttons):
            await self.session.delete(existing)
        await self.session.flush()
        for button in buttons:
            self.session.add(
                DialogButton(
                    window_id=row.id,
                    row=button.row,
                    column=button.column,
                    text_key=button.text_key,
                    link_type=button.link_type,
                    style=button.style,
                    visibility_condition=button.visibility_condition,
                    action_key=button.action_key,
                    target_window_key=button.target_window_key,
                    url_value=button.url_value,
                    is_hidden=button.is_hidden,
                )
            )
        await self.session.flush()
        await self.session.refresh(row)
        return self._to_dto(row)

    async def _claim_free_slot(self) -> int:
        taken = set(
            await self.session.scalars(
                select(DialogWindow.slot).where(DialogWindow.slot.is_not(None))
            )
        )
        for slot in range(CUSTOM_DIALOG_WINDOW_SLOTS):
            if slot not in taken:
                return slot
        raise ValidationError(
            f"No free dialog window slot left (limit is {CUSTOM_DIALOG_WINDOW_SLOTS})",
            code=ErrorCode.DIALOG_WINDOW_LIMIT_REACHED,
        )

    async def create_custom_window(self, key: str, data: CreateCustomWindowDto) -> DialogWindowDto:
        if await self._get_row(key) is not None:
            raise ConflictError(
                f"Dialog window '{key}' already exists", code=ErrorCode.DIALOG_WINDOW_EXISTS
            )
        window = DialogWindow(
            key=key,
            kind=DialogWindowKind.CUSTOM,
            flow=DialogFlow.CUSTOM,
            banner_name=data.banner_name,
            name=data.name,
            is_enabled=True,
            position=data.position,
            slot=await self._claim_free_slot(),
            default_snapshot={"banner_name": None, "buttons": []},
        )
        self.session.add(window)
        await self.session.flush()
        await self.session.refresh(window)
        return self._to_dto(window)

    async def delete_custom_window(self, key: str) -> None:
        row = await self._get_row_or_raise(key)
        await self.session.delete(row)
        await self.session.flush()

    async def seed_action_keys(self, key: str) -> set[str]:
        row = await self._get_row_or_raise(key)
        return {
            b.get("action_key")
            for b in row.default_snapshot.get("buttons", [])
            if b.get("action_key")
        }

    async def is_button_text_key(self, text_key: str) -> bool:
        stmt = select(DialogButton.id).where(DialogButton.text_key == text_key).limit(1)
        return await self.session.scalar(stmt) is not None

    async def system_conditions(self, key: str) -> dict[str, Optional[str]]:
        return _system_conditions(await self._get_row_or_raise(key))

    async def required_conditions(self, key: str) -> dict[str, Optional[str]]:
        row = await self._get_row_or_raise(key)
        required = _required_text_keys(row)
        return {k: v for k, v in _system_conditions(row).items() if k in required}

    async def reset_to_snapshot(self, key: str) -> DialogWindowDto:
        row = await self.session.scalar(
            select(DialogWindow).where(DialogWindow.key == key).with_for_update()
        )
        if row is None:
            raise NotFoundError(
                f"Dialog window '{key}' not found", code=ErrorCode.DIALOG_WINDOW_NOT_FOUND
            )
        snapshot = row.default_snapshot
        raw_banner = snapshot.get("banner_name")
        row.banner_name = raw_banner or None
        row.is_enabled = True
        for existing in list(row.buttons):
            await self.session.delete(existing)
        await self.session.flush()
        for raw in snapshot.get("buttons", []):
            self.session.add(self._button_from_snapshot(row.id, raw))
        await self.session.flush()
        await self.session.refresh(row)
        return self._to_dto(row)


def _system_text_keys(row: DialogWindow) -> set[str]:
    return set(_system_conditions(row))


def _required_text_keys(row: DialogWindow) -> set[str]:
    if row.kind != DialogWindowKind.SEED:
        return set()
    return snapshot_required_keys(row.default_snapshot)


def _system_conditions(row: DialogWindow) -> dict[str, Optional[str]]:
    if row.kind != DialogWindowKind.SEED:
        return {}
    return snapshot_conditions(row.default_snapshot)
