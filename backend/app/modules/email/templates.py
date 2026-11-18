from typing import Optional

from app.core.utils.telegram_text import premium_emoji_glyphs
from app.modules.email.dto import EmailBrandDto
from app.modules.email.layout import render_email


def render_code_email(
    *, title: str, body: str, brand: EmailBrandDto, cabinet_url: Optional[str]
) -> tuple[str, str, str]:
    """(subject, text, html) of a one-time code email; the title doubles as the subject."""
    text, html = render_email(
        brand=brand.brand,
        color=brand.color,
        has_logo=brand.has_logo,
        title=title,
        body=body,
        cabinet_url=cabinet_url,
        button=None,
    )
    return premium_emoji_glyphs(title), text, html
