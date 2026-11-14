import pytest

from app.core.telegram_api import BotApiError
from app.modules.settings import telegram_status
from app.modules.settings.telegram_status import fetch_bot_status, fetch_bot_username
from tests.fakes.telegram_bot import FakeBotSession, refusal, use_fake_bot


async def test_a_refused_token_becomes_a_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = refusal(401, "Unauthorized")
    use_fake_bot(monkeypatch, FakeBotSession({"getWebhookInfo": refused}))

    status = await fetch_bot_status("123:abc", None)

    assert status.is_configured is True
    assert status.error == "Unauthorized"


async def test_a_malformed_token_becomes_a_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    use_fake_bot(monkeypatch, FakeBotSession())

    status = await fetch_bot_status("not-a-token", None)

    assert status.is_configured is True
    assert status.error


def _webhook_info(pending: int) -> dict[str, object]:
    return {
        "url": "https://x",
        "has_custom_certificate": False,
        "pending_update_count": pending,
        "last_error_message": "boom",
        "last_error_date": 1_700_000_000,
    }


async def test_an_error_with_updates_queued_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    session = use_fake_bot(monkeypatch, FakeBotSession({"getWebhookInfo": _webhook_info(3)}))

    status = await fetch_bot_status("123:abc", "socks5://proxy:1080")

    assert status.error == "boom"
    assert status.is_running is False
    assert session.proxy == "socks5://proxy:1080"
    assert session.closed


async def test_an_error_kept_after_delivery_recovered_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_fake_bot(monkeypatch, FakeBotSession({"getWebhookInfo": _webhook_info(0)}))

    status = await fetch_bot_status("123:abc", None)

    assert status.error is None
    assert status.is_running is True


@pytest.fixture(autouse=True)
def _clean_username_caches() -> None:
    telegram_status._USERNAME_CACHE.clear()
    telegram_status._USERNAME_FAILED_UNTIL.clear()


async def test_a_failed_username_lookup_is_not_retried_inside_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1000.0
    session = use_fake_bot(
        monkeypatch, FakeBotSession({"getMe": BotApiError("getMe: ClientConnectorError")})
    )
    monkeypatch.setattr(telegram_status.time, "monotonic", lambda: now)

    assert await fetch_bot_username("123:abc", None) is None
    assert await fetch_bot_username("123:abc", None) is None
    assert len(session.called("getMe")) == 1

    now += 61
    assert await fetch_bot_username("123:abc", None) is None
    assert len(session.called("getMe")) == 2


async def test_the_username_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    me = {"id": 123, "is_bot": True, "first_name": "bot", "username": "shop_bot"}
    session = use_fake_bot(monkeypatch, FakeBotSession({"getMe": me}))

    assert await fetch_bot_username("123:abc", None) == "shop_bot"
    assert await fetch_bot_username("123:abc", None) == "shop_bot"
    assert len(session.called("getMe")) == 1
