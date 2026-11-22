import pytest
from pydantic import BaseModel

from app.modules.transactions.api.schemas import CheckoutQuoteResponse, OwnTransactionResponse
from app.modules.users import CurrentUserResponse, UserResponse


@pytest.mark.parametrize(
    "schema", [CurrentUserResponse, CheckoutQuoteResponse, OwnTransactionResponse]
)
def test_no_customer_facing_answer_carries_the_markup(schema: type[BaseModel]) -> None:
    assert "markup_percent" not in schema.model_fields


def test_the_admin_user_card_carries_it() -> None:
    assert "markup_percent" in UserResponse.model_fields
