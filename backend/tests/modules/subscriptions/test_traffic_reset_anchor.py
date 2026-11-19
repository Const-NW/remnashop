from datetime import datetime, timedelta, timezone

import pytest
from remnapy import RemnawaveSDK

from app.core.db.uow import UnitOfWork
from app.core.exceptions import ValidationError
from app.core.utils.time import datetime_now
from app.modules.subscriptions.dto import SubscriptionDto
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.subscriptions.usage import PanelTrafficUsage
from app.modules.subscriptions.usecases import GetTrafficResetAnchorUseCase
from app.modules.users import UserDto
from app.modules.users.repository import UserRepository
from tests.fakes.remnawave import FakeUserStore, _user_payload
from tests.modules.subscriptions.factories import create_subscription

_PANEL_CREATED = datetime(2025, 3, 31, 12, tzinfo=timezone.utc)


def _use_case(
    subscription_repository: SubscriptionRepository, remnawave_sdk: RemnawaveSDK, uow: UnitOfWork
) -> GetTrafficResetAnchorUseCase:
    return GetTrafficResetAnchorUseCase(
        subscription_repository, PanelTrafficUsage(remnawave_sdk), uow
    )


async def _owned(
    subscription_repository: SubscriptionRepository, user_repository: UserRepository, tg: int
) -> tuple[SubscriptionDto, UserDto]:
    subscription = await create_subscription(
        subscription_repository, user_repository, telegram_id=tg, remnawave_user_id=tg
    )
    owner = await user_repository.find_by_id(subscription.user_id)
    assert owner is not None
    return subscription, owner


def _panel_user(user_id: int) -> dict:
    payload = _user_payload(
        user_id=user_id, username=f"u{user_id}", expire_at=datetime_now() + timedelta(days=30)
    )
    payload["createdAt"] = _PANEL_CREATED.isoformat()
    return payload


async def test_a_stored_anchor_skips_the_panel(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    remnawave_sdk: RemnawaveSDK,
    uow: UnitOfWork,
) -> None:
    subscription, owner = await _owned(subscription_repository, user_repository, 79101)
    await subscription_repository.set_panel_created_at(79101, _PANEL_CREATED)

    # the fake panel has no such user: a lookup would come back empty
    anchor = await _use_case(subscription_repository, remnawave_sdk, uow)(owner, subscription.id)

    assert anchor == _PANEL_CREATED


async def test_a_missing_anchor_is_fetched_and_kept(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    remnawave_sdk: RemnawaveSDK,
    remnawave_store: FakeUserStore,
    uow: UnitOfWork,
) -> None:
    subscription, owner = await _owned(subscription_repository, user_repository, 79102)
    remnawave_store[79102] = _panel_user(79102)

    anchor = await _use_case(subscription_repository, remnawave_sdk, uow)(owner, subscription.id)

    assert anchor == _PANEL_CREATED
    stored = await subscription_repository.get_by_id(subscription.id)
    assert stored.panel_created_at == _PANEL_CREATED


async def test_an_unreachable_panel_gives_no_anchor(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    remnawave_sdk: RemnawaveSDK,
    uow: UnitOfWork,
) -> None:
    subscription, owner = await _owned(subscription_repository, user_repository, 79103)

    anchor = await _use_case(subscription_repository, remnawave_sdk, uow)(owner, subscription.id)

    assert anchor is None
    stored = await subscription_repository.get_by_id(subscription.id)
    assert stored.panel_created_at is None


async def test_someone_elses_subscription_is_refused(
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
    remnawave_sdk: RemnawaveSDK,
    uow: UnitOfWork,
) -> None:
    subscription, _ = await _owned(subscription_repository, user_repository, 79104)
    _, stranger = await _owned(subscription_repository, user_repository, 79105)

    with pytest.raises(ValidationError):
        await _use_case(subscription_repository, remnawave_sdk, uow)(stranger, subscription.id)
