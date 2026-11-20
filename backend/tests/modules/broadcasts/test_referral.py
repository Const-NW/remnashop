from typing import Optional
from unittest.mock import AsyncMock, patch

from pydantic import SecretStr

from app.core.enums import BroadcastButtonKind, Locale
from app.modules.broadcasts.dto import BroadcastButtonDto, BroadcastContentDto, BroadcastDto
from app.modules.broadcasts.referral import (
    NO_REFERRAL_LINKS,
    cabinet_links_allowed,
    referral_link_bases,
    uses_referral_link,
)
from app.modules.referrals import ReferralRepository
from app.modules.referrals.dto import UpdateReferralSettingsDto
from app.modules.settings import BotSettingsDto, SettingsRepository
from tests.modules.broadcasts.test_render import _broadcast

_FETCH = "app.modules.settings.telegram_status.fetch_bot_username"
_CABINET = "https://c.example/"


def _bot(token: Optional[str] = "123:abc") -> BotSettingsDto:
    return BotSettingsDto.model_construct(token=SecretStr(token) if token else None)


def _with_text(text: str) -> BroadcastDto:
    return _broadcast(
        contents={Locale.EN: BroadcastContentDto(text=text)}, buttons=[], promocode_id=None
    )


def test_uses_referral_link() -> None:
    assert uses_referral_link(_with_text("{ $referral_link }"))
    assert not uses_referral_link(_with_text("{ $name }"))
    share = _broadcast(
        contents={Locale.EN: BroadcastContentDto(text="x", button_labels=["Share"])},
        buttons=[BroadcastButtonDto(kind=BroadcastButtonKind.SHARE_REFERRAL)],
        promocode_id=None,
    )
    assert uses_referral_link(share)


async def test_both_bases() -> None:
    with patch(_FETCH, AsyncMock(return_value="shop_bot")):
        bases = await referral_link_bases(True, _bot(), _CABINET)
    assert bases.bot == "https://t.me/shop_bot?start=ref_"
    assert bases.cabinet == "https://c.example/?ref="


async def test_cabinet_base_needs_browser_sign_in_and_an_address() -> None:
    with patch(_FETCH, AsyncMock(return_value="shop_bot")):
        assert (await referral_link_bases(False, _bot(), _CABINET)).cabinet is None
        assert (await referral_link_bases(True, _bot(), "")).cabinet is None


async def test_no_bot_keeps_the_cabinet_base() -> None:
    fetch = AsyncMock(return_value="shop_bot")
    with patch(_FETCH, fetch):
        bases = await referral_link_bases(True, _bot(None), _CABINET)
    assert bases.bot is None and bases.cabinet == "https://c.example/?ref="
    fetch.assert_not_called()


async def test_no_lookup_without_referral_links() -> None:
    fetch = AsyncMock(return_value="shop_bot")
    with patch(_FETCH, fetch):
        assert await referral_link_bases(None, _bot(), _CABINET) is NO_REFERRAL_LINKS
    fetch.assert_not_called()


async def test_cabinet_links_allowed(
    referral_repository: ReferralRepository, settings_repository: SettingsRepository
) -> None:
    broadcast = _with_text("{ $referral_link }")
    assert await cabinet_links_allowed(broadcast, referral_repository, settings_repository) is None

    await referral_repository.update_settings(UpdateReferralSettingsDto(enabled=True))
    assert (
        await cabinet_links_allowed(_with_text("hi"), referral_repository, settings_repository)
        is None
    )
    allowed = await cabinet_links_allowed(broadcast, referral_repository, settings_repository)
    cabinet = await settings_repository.get_cabinet_auth_settings()
    assert allowed is cabinet.has_browser_sign_in
