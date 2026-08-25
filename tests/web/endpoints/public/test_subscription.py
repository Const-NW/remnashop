import pytest
from fastapi import HTTPException, status

from src.application.dto import UserDto
from src.core.enums import AuthType
from src.web.purchase_access import assert_web_payment_allowed


def test_telegram_auth_can_purchase_without_verified_email() -> None:
    user = UserDto(
        auth_type=AuthType.TELEGRAM,
        is_email_verified=False,
        name="Telegram user",
    )

    assert_web_payment_allowed(user)


def test_email_auth_cannot_purchase_without_verified_email() -> None:
    user = UserDto(
        auth_type=AuthType.EMAIL,
        is_email_verified=False,
        name="Email user",
    )

    with pytest.raises(HTTPException) as exc_info:
        assert_web_payment_allowed(user)

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.detail == (
        "Email must be verified before purchasing or extending a subscription"
    )


def test_email_auth_can_purchase_with_verified_email() -> None:
    user = UserDto(
        auth_type=AuthType.EMAIL,
        is_email_verified=True,
        name="Email user",
    )

    assert_web_payment_allowed(user)
