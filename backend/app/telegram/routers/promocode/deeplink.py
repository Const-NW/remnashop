from collections.abc import Awaitable, Callable
from functools import partial
from typing import Final, Optional

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from aiogram_dialog import DialogManager, ShowMode, StartMode
from dishka import FromDishka
from fluentogram import TranslatorHub
from loguru import logger
from redis.asyncio import Redis

from app.core.constants import AD_LINK_START_PREFIX, PROMOCODE_START_PREFIX, REDIS_KEY_PREFIX
from app.core.enums import AdLinkChannel, PromocodeLinkOutcome
from app.core.exceptions import NotFoundError, ValidationError
from app.core.utils.rate_counter import hit_counter
from app.modules.advertising import ActivateAdLinkPromocodeDto, ActivateAdLinkPromocodeUseCase
from app.modules.promocodes import (
    ActivatePromocodeByLinkDto,
    ActivatePromocodeByLinkUseCase,
    ListPromocodeTargetsUseCase,
    PromocodeLinkResultDto,
    ValidatePromocodeUseCase,
)
from app.modules.users import UserDto
from app.telegram.i18n import get_translator
from app.telegram.notice import answer_notice
from app.telegram.routers.promocode.handlers import (
    PROMOCODE_INPUT_LIMIT,
    PROMOCODE_INPUT_WINDOW_S,
    continue_with_code,
    promocode_refusal_key,
)
from app.telegram.states import MainMenu, Promocode

_CODE: Final[str] = "[A-Za-z0-9_-]+"

router = Router(name=__name__)


async def _open_menu_and_apply(
    message: Message,
    dialog_manager: DialogManager,
    user: UserDto,
    i18n: TranslatorHub,
    apply: Callable[[], Awaitable[Optional[PromocodeLinkResultDto]]],
    validate_promocode: ValidatePromocodeUseCase,
    list_promocode_targets: ListPromocodeTargetsUseCase,
) -> None:
    runner = get_translator(i18n, user)
    # what a plain /start shows; the promocode's outcome comes on top of it
    await dialog_manager.start(
        state=MainMenu.MAIN, mode=StartMode.RESET_STACK, show_mode=ShowMode.DELETE_AND_SEND
    )
    try:
        result = await apply()
    except (NotFoundError, ValidationError) as error:
        await answer_notice(message, runner.get(promocode_refusal_key(error)))
        return
    except Exception:
        logger.exception(f"{user.log} Promocode link activation failed unexpectedly")
        await answer_notice(message, runner.get("ntf-promocode.activation-failed"))
        return
    if result is None:
        return
    if result.outcome is PromocodeLinkOutcome.CHOOSE_TARGET:
        await dialog_manager.start(Promocode.INPUT)
        await continue_with_code(
            result.promocode.code,
            user,
            dialog_manager,
            validate_promocode,
            list_promocode_targets,
            reply=partial(answer_notice, message),
            runner=runner,
        )
        return
    if result.outcome is PromocodeLinkOutcome.NO_TARGET:
        # the customer may never have seen the code: a link hid it
        text = runner.get("ntf-promocode.link-no-target", code=result.promocode.code)
    else:
        text = runner.get("ntf-promocode.activated")
    await answer_notice(message, text)


@router.message(
    CommandStart(deep_link=True, magic=F.args.regexp(rf"^{PROMOCODE_START_PREFIX}{_CODE}$"))
)
async def on_promocode_link(
    message: Message,
    command: CommandObject,
    user: UserDto,
    dialog_manager: DialogManager,
    activate: FromDishka[ActivatePromocodeByLinkUseCase],
    validate_promocode: FromDishka[ValidatePromocodeUseCase],
    list_promocode_targets: FromDishka[ListPromocodeTargetsUseCase],
    redis: FromDishka[Redis],
    i18n: FromDishka[TranslatorHub],
) -> None:
    code = (command.args or "").removeprefix(PROMOCODE_START_PREFIX).upper()
    # the code is a guess like any typed one: same counter as the input window
    key = f"{REDIS_KEY_PREFIX}ratelimit:bot_promocode:user:{user.id}"
    if await hit_counter(redis, key, PROMOCODE_INPUT_WINDOW_S) > PROMOCODE_INPUT_LIMIT:
        await answer_notice(
            message, get_translator(i18n, user).get("ntf-promocode.too-many-attempts")
        )
        return
    await _open_menu_and_apply(
        message,
        dialog_manager,
        user,
        i18n,
        partial(activate, user, ActivatePromocodeByLinkDto(code=code)),
        validate_promocode,
        list_promocode_targets,
    )


@router.message(
    CommandStart(deep_link=True, magic=F.args.regexp(rf"^{AD_LINK_START_PREFIX}{_CODE}$"))
)
async def on_ad_link(
    message: Message,
    command: CommandObject,
    user: UserDto,
    dialog_manager: DialogManager,
    activate: FromDishka[ActivateAdLinkPromocodeUseCase],
    validate_promocode: FromDishka[ValidatePromocodeUseCase],
    list_promocode_targets: FromDishka[ListPromocodeTargetsUseCase],
    i18n: FromDishka[TranslatorHub],
) -> None:
    # the attribution itself is the user middleware's, on registration
    code = (command.args or "").removeprefix(AD_LINK_START_PREFIX)
    await _open_menu_and_apply(
        message,
        dialog_manager,
        user,
        i18n,
        partial(
            activate,
            user,
            ActivateAdLinkPromocodeDto(ad_link_code=code, channel=AdLinkChannel.BOT),
        ),
        validate_promocode,
        list_promocode_targets,
    )
