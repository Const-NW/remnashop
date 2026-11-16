from app.core.enums import Locale, PlanType
from app.modules.plans import PlanRepository
from app.modules.plans.dto import CreatePlanDto, PlanTranslationDto
from app.modules.subscriptions import with_current_plan_labels
from app.modules.subscriptions.repository import SubscriptionRepository
from app.modules.translations import TranslationRepository
from app.modules.users.repository import UserRepository
from tests.modules.subscriptions.factories import create_subscription


async def test_an_old_snapshot_shows_the_plans_current_name_and_icon(
    plan_repository: PlanRepository,
    subscription_repository: SubscriptionRepository,
    user_repository: UserRepository,
) -> None:
    await TranslationRepository(plan_repository.session).add_active_locale(Locale.EN)
    plan = await plan_repository.create(
        CreatePlanDto(
            public_code="team",
            name="Командный",
            type=PlanType.BOTH,
            traffic_limit=100,
            device_limit=3,
            icon_emoji_id="5368324170671202286",
            icon_emoji_fallback="💼",
            translations=[PlanTranslationDto(locale=Locale.EN, name="Team", description=None)],
        )
    )
    # written before snapshots carried translations and the icon
    subscription = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=79001,
        remnawave_user_id=79001,
        plan_id=plan.id,
        name="💼 Командный",
    )
    gone = await create_subscription(
        subscription_repository,
        user_repository,
        telegram_id=79002,
        remnawave_user_id=79002,
        plan_id=plan.id + 1000,
        name="Old",
    )

    shown, kept = await with_current_plan_labels([subscription, gone], plan_repository)

    assert shown.plan_snapshot.display_name(Locale.RU) == "Командный"
    assert shown.plan_snapshot.display_name(Locale.EN) == "Team"
    assert shown.plan_snapshot.icon_emoji_id == "5368324170671202286"
    assert shown.plan_snapshot.icon_emoji_fallback == "💼"
    # a deleted plan leaves the snapshot as it was
    assert kept.plan_snapshot == gone.plan_snapshot
