from typing import cast

from dishka import Provider, Scope, provide, provide_all

from app.modules.blacklist import ReconcileBlacklistDispatch
from app.modules.users.blacklist_repository import UserBlacklistRepository
from app.modules.users.repository import UserRepository
from app.modules.users.stats_repository import UserStatsRepository
from app.modules.users.tasks import reconcile_blacklist_task
from app.modules.users.usecases import (
    BindTelegramIdUseCase,
    BlockBlacklistMatchesUseCase,
    BlockUserUseCase,
    ClearEmailUndeliverableUseCase,
    CreateAdminUseCase,
    DeleteAdminUseCase,
    DeleteUserUseCase,
    DemoteAdminUseCase,
    FindTelegramUserUseCase,
    GetAdminUseCase,
    GetCurrentUserUseCase,
    GetUserListStatsUseCase,
    GetUserRegistrationUseCase,
    GetUserUseCase,
    ListAdminsUseCase,
    ListBlacklistMatchesUseCase,
    ListUsersUseCase,
    PromoteToAdminUseCase,
    RegisterCabinetUserUseCase,
    RegisterTelegramUserUseCase,
    ResetBotMenuUseCase,
    SetBotBlockedUseCase,
    SetMarketingOptOutUseCase,
    SetUserDiscountUseCase,
    SetUserMarkupUseCase,
    SetUserTrialAvailabilityUseCase,
    UnbindTelegramIdUseCase,
    UnblockBlacklistMatchesUseCase,
    UnblockUserUseCase,
    UnsubscribeMarketingUseCase,
    UpdateAdminUseCase,
    UpdateOwnProfileUseCase,
    UpdateOwnSecuritySettingsUseCase,
)


class UsersProvider(Provider):
    scope = Scope.REQUEST

    repositories = provide_all(
        UserRepository,
        UserStatsRepository,
        UserBlacklistRepository,
    )
    use_cases = provide_all(
        GetCurrentUserUseCase,
        ListAdminsUseCase,
        GetAdminUseCase,
        ListUsersUseCase,
        GetUserUseCase,
        GetUserRegistrationUseCase,
        GetUserListStatsUseCase,
        ListBlacklistMatchesUseCase,
        CreateAdminUseCase,
        PromoteToAdminUseCase,
        UpdateAdminUseCase,
        DemoteAdminUseCase,
        DeleteAdminUseCase,
        DeleteUserUseCase,
        UpdateOwnProfileUseCase,
        UpdateOwnSecuritySettingsUseCase,
        RegisterTelegramUserUseCase,
        FindTelegramUserUseCase,
        RegisterCabinetUserUseCase,
        SetBotBlockedUseCase,
        SetMarketingOptOutUseCase,
        SetUserDiscountUseCase,
        SetUserMarkupUseCase,
        SetUserTrialAvailabilityUseCase,
        BindTelegramIdUseCase,
        UnbindTelegramIdUseCase,
        BlockUserUseCase,
        UnblockUserUseCase,
        ClearEmailUndeliverableUseCase,
        ResetBotMenuUseCase,
        UnsubscribeMarketingUseCase,
        UnblockBlacklistMatchesUseCase,
        BlockBlacklistMatchesUseCase,
    )

    @provide(scope=Scope.APP)
    def reconcile_blacklist_dispatch(self) -> ReconcileBlacklistDispatch:
        return cast(ReconcileBlacklistDispatch, reconcile_blacklist_task)


def get_users_providers() -> list[Provider]:
    return [UsersProvider()]
