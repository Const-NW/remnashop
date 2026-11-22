from typing import Optional

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import EventOutboxEntry
from app.core.db.uow import UnitOfWork
from app.core.enums import Locale, Role
from app.core.error_codes import ErrorCode
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.ports import EventPublisher
from app.modules.users.dto import CreateUserDto, ResetBotMenuDto, UserDto
from app.modules.users.repository import UserRepository
from app.modules.users.usecases import ResetBotMenuUseCase


class _Signals:
    def __init__(self, receivers: int = 1) -> None:
        self.receivers = receivers
        self.reset: list[int] = []

    async def reset_customer_menu(self, telegram_id: int) -> int:
        self.reset.append(telegram_id)
        return self.receivers


@pytest.fixture
async def owner(user_repository: UserRepository, uow: UnitOfWork) -> UserDto:
    async with uow:
        return await user_repository.create(
            CreateUserDto(login="reset-owner", name="owner", role=Role.OWNER, language=Locale.EN)
        )


def _use_case(
    user_repository: UserRepository, uow: UnitOfWork, event_publisher: EventPublisher, signals
) -> ResetBotMenuUseCase:
    return ResetBotMenuUseCase(user_repository, signals, uow, event_publisher)


async def _events(session: AsyncSession) -> list[str]:
    return list(await session.scalars(select(EventOutboxEntry.event_type)))


async def test_resets_the_customers_menu_and_records_it(
    owner: UserDto,
    user_repository: UserRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    session: AsyncSession,
) -> None:
    async with uow:
        customer = await user_repository.create(CreateUserDto(name="stuck", telegram_id=555_001))
    signals = _Signals()

    await _use_case(user_repository, uow, event_publisher, signals)(
        owner, ResetBotMenuDto(user_id=customer.id, client_ip="127.0.0.1")
    )

    assert signals.reset == [555_001]
    assert "UserBotMenuResetEvent" in await _events(session)


@pytest.mark.parametrize(
    ("telegram_id", "blocked", "bot_blocked", "code"),
    [
        (None, False, False, ErrorCode.USER_NO_TELEGRAM),
        (555_002, True, False, ErrorCode.USER_TARGET_BLOCKED),
        (555_004, False, True, ErrorCode.USER_TARGET_BOT_BLOCKED),
    ],
    ids=["no-telegram", "blocked", "bot-blocked"],
)
async def test_refuses_a_customer_the_bot_cant_reach(
    telegram_id: Optional[int],
    blocked: bool,
    bot_blocked: bool,
    code: ErrorCode,
    owner: UserDto,
    user_repository: UserRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    session: AsyncSession,
) -> None:
    async with uow:
        customer = await user_repository.create(
            CreateUserDto(name="unreachable", telegram_id=telegram_id, email="u@mail.ru")
        )
        if blocked:
            await user_repository.set_blocked(customer.id, True)
        if bot_blocked:
            await user_repository.set_bot_blocked(customer.id, True)
    signals = _Signals()

    with pytest.raises(ValidationError) as error:
        await _use_case(user_repository, uow, event_publisher, signals)(
            owner, ResetBotMenuDto(user_id=customer.id, client_ip="127.0.0.1")
        )

    assert error.value.code is code
    assert signals.reset == []
    assert "UserBotMenuResetEvent" not in await _events(session)


async def test_says_so_when_no_bot_process_took_the_signal(
    owner: UserDto,
    user_repository: UserRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
    session: AsyncSession,
) -> None:
    async with uow:
        customer = await user_repository.create(CreateUserDto(name="idle", telegram_id=555_003))

    with pytest.raises(ConflictError) as error:
        await _use_case(user_repository, uow, event_publisher, _Signals(receivers=0))(
            owner, ResetBotMenuDto(user_id=customer.id, client_ip="127.0.0.1")
        )

    assert error.value.code is ErrorCode.BOT_NOT_RUNNING
    assert "UserBotMenuResetEvent" not in await _events(session)


async def test_an_admin_cant_reach_the_owner(
    owner: UserDto,
    user_repository: UserRepository,
    uow: UnitOfWork,
    event_publisher: EventPublisher,
) -> None:
    async with uow:
        admin = await user_repository.create(
            CreateUserDto(login="reset-admin", name="admin", role=Role.ADMIN, language=Locale.EN)
        )
        await user_repository.set_telegram_id(owner.id, 555_005)
    signals = _Signals()

    with pytest.raises(NotFoundError):
        await _use_case(user_repository, uow, event_publisher, signals)(
            admin, ResetBotMenuDto(user_id=owner.id, client_ip="127.0.0.1")
        )
    assert signals.reset == []
