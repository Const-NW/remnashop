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

# the customer has a step to take: a subscription to pick, or a reusable code to confirm
_ASKS_CUSTOMER: Final[frozenset[PromocodeLinkOutcome]] = frozenset(
    {PromocodeLinkOutcome.CHOOSE_TARGET, PromocodeLinkOutcome.CONFIRM}
)

router = Router(name=__name__)


def _attempts_key(user: UserDto) -> str:
    # a link's code is a guess like a typed one: same counter as the input window
    return f"{REDIS_KEY_PREFIX}ratelimit:bot_promocode:user:{user.id}"


async def _over_limit(redis: Redis, user: UserDto) -> bool:
    return (
        await hit_counter(redis, _attempts_key(user), PROMOCODE_INPUT_WINDOW_S)
        > PROMOCODE_INPUT_LIMIT
    )


async def _apply_and_open_menu(
    message: Message,
    dialog_manager: DialogManager,
    user: UserDto,
    i18n: TranslatorHub,
    apply: Callable[[], Awaitable[Optional[PromocodeLinkResultDto]]],
    validate_promocode: ValidatePromocodeUseCase,
    list_promocode_targets: ListPromocodeTargetsUseCase,
    *,
    tell_refusal: bool,
) -> None:
    runner = get_translator(i18n, user)
    notice: Optional[str] = None
    result: Optional[PromocodeLinkResultDto] = None
    # applied before the menu is drawn, so the menu already shows what it gave
    try:
        result = await apply()
    except (NotFoundError, ValidationError) as error:
        notice = runner.get(promocode_refusal_key(error)) if tell_refusal else None
    except Exception:
        logger.exception(f"{user.log} Promocode link activation failed unexpectedly")
        notice = runner.get("ntf-promocode.activation-failed") if tell_refusal else None

    if result is not None and result.outcome in _ASKS_CUSTOMER:
        # the promocode dialog over the menu, drawn once on the step it lands on
        await dialog_manager.start(
            state=MainMenu.MAIN, mode=StartMode.RESET_STACK, show_mode=ShowMode.NO_UPDATE
        )
        await dialog_manager.start(Promocode.INPUT, show_mode=ShowMode.NO_UPDATE)
        await continue_with_code(
            result.promocode.code,
            user,
            dialog_manager,
            validate_promocode,
            list_promocode_targets,
            reply=partial(answer_notice, message),
            runner=runner,
        )
        # show() reads the manager's own mode, which the starts above left at NO_UPDATE
        dialog_manager.show_mode = ShowMode.DELETE_AND_SEND
        await dialog_manager.show()
        return

    # what a plain /start shows, with the outcome under it
    await dialog_manager.start(
        state=MainMenu.MAIN, mode=StartMode.RESET_STACK, show_mode=ShowMode.DELETE_AND_SEND
    )
    if result is not None and result.outcome is PromocodeLinkOutcome.NO_TARGET:
        # the customer may never have seen the code: a link hid it
        notice = runner.get("ntf-promocode.link-no-target", code=result.promocode.code)
    elif result is not None:
        notice = runner.get("ntf-promocode.activated")
    if notice is not None:
        await answer_notice(message, notice)


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
    if await _over_limit(redis, user):
        text = get_translator(i18n, user).get("ntf-promocode.too-many-attempts")
        await answer_notice(message, text)
        return
    code = (command.args or "").removeprefix(PROMOCODE_START_PREFIX).upper()
    await _apply_and_open_menu(
        message,
        dialog_manager,
        user,
        i18n,
        partial(activate, user, ActivatePromocodeByLinkDto(code=code)),
        validate_promocode,
        list_promocode_targets,
        tell_refusal=True,
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
    redis: FromDishka[Redis],
    i18n: FromDishka[TranslatorHub],
) -> None:
    # the attribution itself is the user middleware's, on registration
    code = (command.args or "").removeprefix(AD_LINK_START_PREFIX)

    async def apply() -> Optional[PromocodeLinkResultDto]:
        # only a link that carries a promocode counts as an attempt: opening the
        # shop's plain ad links never locks the customer out of typing a code
        if int(await redis.get(_attempts_key(user)) or 0) >= PROMOCODE_INPUT_LIMIT:
            return None
        dto = ActivateAdLinkPromocodeDto(ad_link_code=code, channel=AdLinkChannel.BOT)
        try:
            result = await activate(user, dto)
        except Exception:
            await hit_counter(redis, _attempts_key(user), PROMOCODE_INPUT_WINDOW_S)
            raise
        if result is not None:
            await hit_counter(redis, _attempts_key(user), PROMOCODE_INPUT_WINDOW_S)
        return result

    await _apply_and_open_menu(
        message,
        dialog_manager,
        user,
        i18n,
        apply,
        validate_promocode,
        list_promocode_targets,
        # an ad link opened again (a used code, one for newcomers only) isn't
        # worth a message: the customer didn't ask for a promocode
        tell_refusal=False,
    )
