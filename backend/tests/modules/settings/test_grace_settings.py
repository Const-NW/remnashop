import datetime
from datetime import timedelta, timezone
from typing import AsyncIterator

import httpx
import pytest
from dishka import AsyncContainer
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import AppConfig
from app.core.constants import API_V1
from app.core.db.models.event_outbox import EventOutboxEntry
from app.core.db.uow import UnitOfWork
from app.core.enums import Locale, Role, SessionAudience
from app.core.security import create_access_token, hash_password, hash_refresh_token
from app.core.security.permissions import Permission
from app.core.utils.time import datetime_now
from app.modules.auth.dto import CreateSessionDto
from app.modules.auth.repository import SessionRepository
from app.modules.settings.dto import UpdateGraceSettingsDto
from app.modules.settings.repository import SettingsRepository
from app.modules.subscriptions.grace import grace_until_for
from app.modules.subscriptions.unlimited import is_unlimited_expiry
from app.modules.users.dto import CreateUserDto
from app.modules.users.repository import UserRepository

_URL = f"{API_V1}/settings/grace"


@pytest.fixture
async def client(
    test_container: AsyncContainer, api_app: FastAPI
) -> AsyncIterator[httpx.AsyncClient]:
    api_app.state.dishka_container = test_container
    transport = httpx.ASGITransport(app=api_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


async def _headers(
    role: Role,
    login: str,
    user_repository: UserRepository,
    session_repository: SessionRepository,
    uow: UnitOfWork,
    config: AppConfig,
    permission_revokes: frozenset[Permission] = frozenset(),
) -> dict[str, str]:
    async with uow:
        user = await user_repository.create(
            CreateUserDto(
                login=login,
                password_hash=await hash_password("x", config.crypt_key.get_secret_value()),
                name=login,
                role=role,
                language=Locale.EN,
                permission_revokes=[p.name for p in permission_revokes],
            )
        )
        await uow.commit()
    session = await session_repository.create(
        CreateSessionDto(
            user_id=user.id,
            refresh_token_hash=hash_refresh_token(f"refresh-{login}"),
            ip_address="127.0.0.1",
            user_agent=None,
            expires_at=datetime.datetime.now(timezone.utc) + timedelta(days=30),
            audience=SessionAudience.DASHBOARD,
        )
    )
    token = create_access_token(user.id, session.id, config.jwt_secret.get_secret_value())
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def owner_headers(
    user_repository: UserRepository,
    session_repository: SessionRepository,
    uow: UnitOfWork,
    config: AppConfig,
) -> dict[str, str]:
    return await _headers(
        Role.OWNER, "graceowner", user_repository, session_repository, uow, config
    )


async def test_defaults_when_no_row(settings_repository: SettingsRepository) -> None:
    settings = await settings_repository.get_grace_settings()

    assert settings.enabled is False
    assert settings.traffic_limit_mb == 0
    # switching grace on alone must not hand out indefinite grace
    assert settings.duration_days == 3


async def test_switching_on_alone_keeps_the_default_duration_and_zero_stays_allowed(
    settings_repository: SettingsRepository,
) -> None:
    switched_on = await settings_repository.update_grace_settings(
        UpdateGraceSettingsDto(enabled=True, fields_set=frozenset({"enabled"}))
    )
    indefinite = await settings_repository.update_grace_settings(
        UpdateGraceSettingsDto(duration_days=0, fields_set=frozenset({"duration_days"}))
    )

    assert switched_on.duration_days == 3
    assert indefinite.duration_days == 0
    assert is_unlimited_expiry(grace_until_for(indefinite, datetime_now()))


async def test_update_persists_and_publishes_changed_fields(
    client: httpx.AsyncClient, owner_headers: dict[str, str], session: AsyncSession
) -> None:
    response = await client.patch(
        _URL,
        json={"enabled": True, "traffic_limit_mb": 512, "duration_days": 7},
        headers=owner_headers,
    )
    assert response.status_code == 200

    body = (await client.get(_URL, headers=owner_headers)).json()
    assert (body["enabled"], body["traffic_limit_mb"], body["duration_days"]) == (True, 512, 7)

    entry = (
        await session.scalars(
            select(EventOutboxEntry).where(
                EventOutboxEntry.event_type == "GraceSettingsChangedEvent"
            )
        )
    ).one()
    assert sorted(entry.payload["changed_fields"]) == [
        "duration_days",
        "enabled",
        "traffic_limit_mb",
    ]


@pytest.mark.parametrize("field", ["traffic_limit_mb", "duration_days"])
async def test_rejects_negative(
    client: httpx.AsyncClient, owner_headers: dict[str, str], field: str
) -> None:
    response = await client.patch(_URL, json={field: -1}, headers=owner_headers)

    assert response.status_code == 400
    assert response.json()["code"] == "GRACE_SETTINGS_INVALID"


async def test_requires_manage_permission(
    client: httpx.AsyncClient,
    user_repository: UserRepository,
    session_repository: SessionRepository,
    uow: UnitOfWork,
    config: AppConfig,
) -> None:
    headers = await _headers(
        Role.ADMIN,
        "graceadmin",
        user_repository,
        session_repository,
        uow,
        config,
        permission_revokes=frozenset({Permission.GRACE_SETTINGS_MANAGE}),
    )

    assert (await client.get(_URL, headers=headers)).status_code == 200
    assert (await client.patch(_URL, json={"enabled": True}, headers=headers)).status_code == 403


@pytest.mark.parametrize("tag", ["lower case", "x" * 100])
async def test_rejects_invalid_tag(
    client: httpx.AsyncClient, owner_headers: dict[str, str], tag: str
) -> None:
    response = await client.patch(_URL, json={"tag": tag}, headers=owner_headers)

    assert response.status_code == 422


async def test_partial_update_keeps_unsent_fields_and_null_clears(
    client: httpx.AsyncClient, owner_headers: dict[str, str]
) -> None:
    sent = await client.patch(
        _URL, json={"tag": "GRACE", "duration_days": 3}, headers=owner_headers
    )
    assert sent.status_code == 200

    kept = (await client.patch(_URL, json={"enabled": True}, headers=owner_headers)).json()
    assert (kept["tag"], kept["duration_days"]) == ("GRACE", 3)

    cleared = (await client.patch(_URL, json={"tag": None}, headers=owner_headers)).json()
    assert (cleared["tag"], cleared["duration_days"]) == (None, 3)
