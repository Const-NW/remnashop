from app.modules.subscriptions.api.schemas import PublicPlanResponse


def test_premium_emoji_markup_becomes_its_glyph() -> None:
    description = PublicPlanResponse.model_validate(
        {
            "id": 1,
            "name": "Plan",
            "description": "[\U0001f525](emoji:42) fast",
            "type": "UNLIMITED",
            "traffic_limit_strategy": "NO_RESET",
            "traffic_limit": 0,
            "device_limit": 0,
            "is_trial": False,
            "order_index": 0,
            "durations": [],
        }
    ).description

    assert description == "\U0001f525 fast"
