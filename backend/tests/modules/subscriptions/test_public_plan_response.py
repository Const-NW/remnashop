from app.modules.subscriptions.api.schemas import PublicPlanResponse


def test_premium_emoji_markup_becomes_its_glyph_and_stays_in_markup() -> None:
    plan = PublicPlanResponse.model_validate(
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
    )

    assert plan.description == "\U0001f525 fast"
    assert plan.model_dump()["description_markup"] == "[\U0001f525](emoji:42) fast"
