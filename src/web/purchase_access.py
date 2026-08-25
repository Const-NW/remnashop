from fastapi import HTTPException, status

from src.application.dto import UserDto
from src.core.enums import AuthType


def assert_web_purchase_email_verified(user: UserDto) -> None:
    if user.auth_type == AuthType.TELEGRAM or user.is_email_verified:
        return

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Email must be verified before purchasing or extending a subscription",
    )
