from contextlib import suppress
from typing import Final, Literal, Union

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InaccessibleMessage, InputRichMessage, Message
from aiogram_dialog import DialogManager
from aiogram_dialog.widgets.kbd import Keyboard
from dishka import FromDishka
from dishka.integrations.aiogram_dialog import inject
from fluentogram import TranslatorHub, TranslatorRunner
from loguru import logger
from redis.asyncio import Redis

from app.core.constants import REDIS_KEY_PREFIX, TIME_1D, USER_KEY
from app.core.enums import PurchaseChannel
from app.modules.legal import (
    AcceptLegalDocumentsDto,
    AcceptLegalDocumentsUseCase,
    LegalRevisionDto,
    ListVisibleLegalDocumentsUseCase,
)
from app.modules.settings import GetBotRequirementsUseCase, ReportBotStatusUseCase
from app.modules.users import UserDto
from app.telegram.dialogs.rendering import render_window_text
from app.telegram.i18n import get_translator
from app.telegram.legal_gate import (
    LegalAcceptCallback,
    LegalDocumentCallback,
    legal_gate_keyboard,
)
from app.telegram.middlewares.requirements import ask_requirement
from app.telegram.notice import delete_or_strip
from app.telegram.routers.legal.keyboard import close_keyboard, legal_keyboard
from app.telegram.routers.requirements.handlers import continue_requirements

router = Router(name=__name__)

# a bot can't delete its messages older than 48 hours
_SENT_TTL_S: Final[int] = 2 * TIME_1D


async def _replace_sent(
    bot: Bot, redis: Redis, user: UserDto, slot: Literal["list", "document"], sent: Message
) -> None:
    """Keeps one list and one document in the chat: the previous copy is deleted."""
    key = f"{REDIS_KEY_PREFIX}legal_sent:{slot}:{user.id}"
    previous = await redis.set(key, sent.message_id, ex=_SENT_TTL_S, get=True)
    if previous is None:
        return
    # already closed by the user or too old to delete
    with suppress(TelegramAPIError):
        await bot.delete_message(chat_id=sent.chat.id, message_id=int(previous))


async def send_legal_list(
    message: Union[Message, InaccessibleMessage],
    user: UserDto,
    list_visible_legal_documents: ListVisibleLegalDocumentsUseCase,
    i18n: TranslatorHub,
    redis: Redis,
) -> bool:
    runner = get_translator(i18n, user)
    documents = await list_visible_legal_documents.execute(PurchaseChannel.BOT, user.language)
    if not documents:
        return False
    sent = await message.answer(
        render_window_text(runner, "msg-legal-list"),
        parse_mode=ParseMode.HTML,
        reply_markup=legal_keyboard(runner, documents),
    )
    await _replace_sent(message.bot, redis, user, "list", sent)  # type: ignore[arg-type]
    return True


@router.message(Command("terms"))
async def on_terms_command(
    message: Message,
    user: UserDto,
    list_visible_legal_documents: FromDishka[ListVisibleLegalDocumentsUseCase],
    i18n: FromDishka[TranslatorHub],
    redis: FromDishka[Redis],
) -> None:
    logger.info(f"{user.log} Called '/terms' command")
    await send_legal_list(message, user, list_visible_legal_documents, i18n, redis)


@inject
async def on_legal_documents(
    callback: CallbackQuery,
    widget: Keyboard,
    dialog_manager: DialogManager,
    list_visible_legal_documents: FromDishka[ListVisibleLegalDocumentsUseCase],
    i18n: FromDishka[TranslatorHub],
    redis: FromDishka[Redis],
) -> None:
    if callback.message is None:
        return
    user: UserDto = dialog_manager.middleware_data[USER_KEY]
    if not await send_legal_list(callback.message, user, list_visible_legal_documents, i18n, redis):
        # all of them unpublished since the menu was drawn: redrawn without the button
        await dialog_manager.show()


@router.callback_query(LegalDocumentCallback.filter())
async def on_legal_document(
    callback: CallbackQuery,
    callback_data: LegalDocumentCallback,
    user: UserDto,
    list_visible_legal_documents: FromDishka[ListVisibleLegalDocumentsUseCase],
    i18n: FromDishka[TranslatorHub],
    redis: FromDishka[Redis],
) -> None:
    documents = await list_visible_legal_documents.execute(PurchaseChannel.BOT, user.language)
    document = next((d for d in documents if d.kind == callback_data.kind), None)
    runner = get_translator(i18n, user)
    if document is None:
        await callback.answer()
        await _refresh_list(callback, documents, runner)
        return
    bot: Bot = callback.bot  # type: ignore[assignment]
    sent = await bot.send_rich_message(
        chat_id=callback.from_user.id,
        rich_message=InputRichMessage(markdown=f"# {document.title}\n\n{document.body}"),
        reply_markup=close_keyboard(runner),
    )
    await _replace_sent(bot, redis, user, "document", sent)
    await callback.answer()


async def _refresh_list(
    callback: CallbackQuery, documents: list[LegalRevisionDto], runner: TranslatorRunner
) -> None:
    message = callback.message
    if not isinstance(message, Message) or message.reply_markup is None:
        return
    # the acceptance prompt has its own flow; only the plain list is redrawn
    if any(
        (button.callback_data or "").startswith(f"{LegalAcceptCallback.__prefix__}:")
        for row in message.reply_markup.inline_keyboard
        for button in row
    ):
        return
    if not documents:
        await delete_or_strip(message)
        return
    with suppress(TelegramAPIError):
        await message.edit_reply_markup(reply_markup=legal_keyboard(runner, documents))


@router.callback_query(LegalAcceptCallback.filter())
async def on_legal_accept(
    callback: CallbackQuery,
    callback_data: LegalAcceptCallback,
    user: UserDto,
    dialog_manager: DialogManager,
    accept_legal_documents: FromDishka[AcceptLegalDocumentsUseCase],
    get_requirements: FromDishka[GetBotRequirementsUseCase],
    redis: FromDishka[Redis],
    i18n: FromDishka[TranslatorHub],
    report: FromDishka[ReportBotStatusUseCase],
    dispatcher: Dispatcher,
) -> None:
    revision_ids = [int(part) for part in callback_data.ids.split(",") if part.isdigit()]
    pending = await accept_legal_documents.execute(
        AcceptLegalDocumentsDto(
            user=user,
            channel=PurchaseChannel.BOT,
            locale=user.language,
            revision_ids=revision_ids,
            client_ip=None,
        )
    )
    if pending.documents:
        runner = get_translator(i18n, user)
        if isinstance(callback.message, Message):
            await delete_or_strip(callback.message)
        await ask_requirement(
            callback,
            render_window_text(
                runner, "msg-legal-gate-update" if pending.is_update else "msg-legal-gate"
            ),
            legal_gate_keyboard(runner, pending),
        )
        return
    requirements = await get_requirements.system()
    await continue_requirements(
        callback, user, dialog_manager, requirements, redis, i18n, report, dispatcher
    )
