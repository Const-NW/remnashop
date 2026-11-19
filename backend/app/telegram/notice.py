import asyncio
from contextlib import suppress
from typing import Any, Final, Optional

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, TelegramObject

from app.core.utils.telegram_text import plain_to_html, premium_emoji_glyphs

_NOTICE_TTL_S: Final[int] = 5
# keeps the deletion tasks referenced until they finish
_deletions: Final[set[asyncio.Task[Any]]] = set()


async def _delete_after(message: Message, seconds: int) -> None:
    await asyncio.sleep(seconds)
    with suppress(TelegramAPIError):
        await message.delete()


async def delete_or_strip(message: Message) -> None:
    """A bot can't delete its message older than 48 hours: its buttons go instead."""
    try:
        await message.delete()
    except TelegramAPIError:
        # already gone, e.g. a double tap
        with suppress(TelegramAPIError):
            await message.edit_reply_markup(reply_markup=None)


def delete_later(message: Message, seconds: int) -> None:
    """Removes a sent message after `seconds`; a restart before then leaves it in the chat."""
    task = asyncio.create_task(_delete_after(message, seconds))
    _deletions.add(task)
    task.add_done_callback(_deletions.discard)


async def answer_notice(
    event: Optional[TelegramObject],
    text: str,
    *,
    keep: bool = False,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> None:
    # a restart within the TTL leaves the message in the chat
    body = f"<i>{plain_to_html(text)}</i>"
    if isinstance(event, Message):
        sent = await event.answer(body, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    elif isinstance(event, CallbackQuery) and event.bot is not None:
        try:
            sent = await event.bot.send_message(
                event.from_user.id, body, parse_mode=ParseMode.HTML, reply_markup=reply_markup
            )
        except TelegramAPIError:
            # a press on a channel post by someone the bot can't write to
            await event.answer(premium_emoji_glyphs(text), show_alert=True)
            return
        await event.answer()
    else:
        return
    if keep:
        return
    delete_later(sent, _NOTICE_TTL_S)
