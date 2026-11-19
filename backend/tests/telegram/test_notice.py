import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiogram.types import CallbackQuery, Message

import app.telegram.notice as notice
from app.telegram.notice import answer_notice, delete_or_strip


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notice, "_NOTICE_TTL_S", 0)


async def _drain() -> None:
    await asyncio.gather(*notice._deletions)


async def test_message_gets_an_italic_escaped_reply_that_is_deleted() -> None:
    sent = MagicMock(spec=Message)
    sent.delete = AsyncMock()
    message = MagicMock(spec=Message)
    message.answer = AsyncMock(return_value=sent)

    await answer_notice(message, "🚧 a < b")
    await _drain()

    message.answer.assert_awaited_once_with(
        "<i>🚧 a &lt; b</i>", parse_mode=ParseMode.HTML, reply_markup=None
    )
    sent.delete.assert_awaited_once()


async def test_button_press_gets_a_message_instead_of_an_alert() -> None:
    sent = MagicMock(spec=Message)
    sent.delete = AsyncMock()
    callback = MagicMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.from_user = MagicMock(id=42)
    callback.bot = MagicMock()
    callback.bot.send_message = AsyncMock(return_value=sent)

    await answer_notice(callback, "❌ Denied.")
    await _drain()

    callback.answer.assert_awaited_once_with()
    callback.bot.send_message.assert_awaited_once_with(
        42, "<i>❌ Denied.</i>", parse_mode=ParseMode.HTML, reply_markup=None
    )
    sent.delete.assert_awaited_once()


async def test_premium_emoji_markup_becomes_a_tg_emoji() -> None:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock(return_value=MagicMock(spec=Message, delete=AsyncMock()))

    await answer_notice(message, "[🚫](emoji:5872829476143894491) Gone")
    await _drain()

    message.answer.assert_awaited_once_with(
        '<i><tg-emoji emoji-id="5872829476143894491">🚫</tg-emoji> Gone</i>',
        parse_mode=ParseMode.HTML,
        reply_markup=None,
    )


def _undeletable() -> TelegramBadRequest:
    return TelegramBadRequest(
        DeleteMessage(chat_id=1, message_id=1), "message can't be deleted for everyone"
    )


async def test_delete_or_strip_deletes_the_message() -> None:
    message = MagicMock(spec=Message, delete=AsyncMock(), edit_reply_markup=AsyncMock())

    await delete_or_strip(message)

    message.delete.assert_awaited_once()
    message.edit_reply_markup.assert_not_awaited()


async def test_a_message_too_old_to_delete_loses_its_buttons() -> None:
    message = MagicMock(
        spec=Message, delete=AsyncMock(side_effect=_undeletable()), edit_reply_markup=AsyncMock()
    )

    await delete_or_strip(message)

    message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)


async def test_a_message_already_gone_is_left_alone() -> None:
    message = MagicMock(
        spec=Message,
        delete=AsyncMock(side_effect=_undeletable()),
        edit_reply_markup=AsyncMock(side_effect=_undeletable()),
    )

    await delete_or_strip(message)
