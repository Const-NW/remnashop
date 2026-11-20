from typing import Final, Optional
from urllib.parse import quote

from loguru import logger

from app.core.constants import REFERRAL_START_PREFIX, T_ME
from app.core.enums import BroadcastButtonKind
from app.modules.broadcasts.content import used_variables
from app.modules.broadcasts.dto import BroadcastDto, ReferralLinkBasesDto
from app.modules.referrals import ReferralRepository
from app.modules.settings import BotSettingsDto, SettingsRepository, bot_username

REFERRAL_LINK_VARIABLE: Final[str] = "referral_link"
NO_REFERRAL_LINKS: Final[ReferralLinkBasesDto] = ReferralLinkBasesDto()
_SHARE_URL: Final[str] = f"{T_ME}share/url?url="
# the cabinet reads ?ref= on any page and keeps it through sign-up
_CABINET_REFERRAL_QUERY: Final[str] = "/?ref="


def uses_referral_link(broadcast: BroadcastDto) -> bool:
    texts = [c.text for c in broadcast.contents.values()] + [
        part for c in broadcast.email_contents.values() for part in (c.subject, c.html)
    ]
    return any(b.kind is BroadcastButtonKind.SHARE_REFERRAL for b in broadcast.buttons) or any(
        REFERRAL_LINK_VARIABLE in used_variables(text) for text in texts
    )


async def cabinet_links_allowed(
    broadcast: BroadcastDto,
    referral_repository: ReferralRepository,
    settings_repository: SettingsRepository,
) -> Optional[bool]:
    """Inside the transaction. None: no referral links at all; otherwise whether the
    cabinet one works, which needs a way to sign in from a browser."""
    if not uses_referral_link(broadcast):
        return None
    if not (await referral_repository.get_settings()).enabled:
        return None
    return (await settings_repository.get_cabinet_auth_settings()).has_browser_sign_in


async def referral_link_bases(
    cabinet_allowed: Optional[bool], bot: BotSettingsDto, cabinet_url: str
) -> ReferralLinkBasesDto:
    """Links without the code; a missing one is skipped when the link is chosen."""
    if cabinet_allowed is None:
        return NO_REFERRAL_LINKS
    username = await bot_username(bot)
    if username is None and bot.token is not None:
        logger.warning("Bot username unknown: broadcast referral links go out without the bot's")
    return ReferralLinkBasesDto(
        bot=f"{T_ME}{username}?start={REFERRAL_START_PREFIX}" if username else None,
        cabinet=(
            cabinet_url.rstrip("/") + _CABINET_REFERRAL_QUERY
            if cabinet_allowed and cabinet_url
            else None
        ),
    )


def share_url(link: str) -> str:
    return _SHARE_URL + quote(link, safe="")
