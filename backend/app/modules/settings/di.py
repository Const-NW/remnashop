from dishka import Provider, Scope, provide, provide_all

from app.modules.settings.bot_commands_sync import BotCommandsPusher
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.usecases import (
    DeleteAppearanceAppIconUseCase,
    DeleteAppearanceFaviconUseCase,
    DeleteAppearanceLogoUseCase,
    GetAccessSettingsUseCase,
    GetBotCommandsUseCase,
    GetBotLogsUseCase,
    GetBotRequirementsUseCase,
    GetBotSettingsUseCase,
    GetBotStatusUseCase,
    GetCabinetAuthSettingsUseCase,
    GetCabinetSettingsUseCase,
    GetEmailSettingsUseCase,
    GetGraceSettingsUseCase,
    GetOAuthSettingsUseCase,
    GetPublicAppearanceImageUseCase,
    GetPublicAppearanceUseCase,
    GetShopSettingsUseCase,
    RemoveBotProxyUseCase,
    ReportBotStatusUseCase,
    ResetAppearanceSettingsUseCase,
    ResetBulkEmailSettingsUseCase,
    ResetEmailSettingsUseCase,
    RestartBotUseCase,
    SetBotProxyUseCase,
    UpdateAccessSettingsUseCase,
    UpdateAppearanceSettingsUseCase,
    UpdateBotCommandsUseCase,
    UpdateBotRequirementsUseCase,
    UpdateBotSettingsUseCase,
    UpdateCabinetAuthSettingsUseCase,
    UpdateEmailSettingsUseCase,
    UpdateGraceSettingsUseCase,
    UpdateOAuthSettingsUseCase,
    UpdateShopSettingsUseCase,
    UploadAppearanceAppIconUseCase,
    UploadAppearanceFaviconUseCase,
    UploadAppearanceLogoUseCase,
)
from app.modules.translations import BotCommandsSync


class SettingsProvider(Provider):
    scope = Scope.REQUEST

    settings_repository = provide(SettingsRepository)
    use_cases = provide_all(
        GetBotSettingsUseCase,
        GetBotStatusUseCase,
        GetBotLogsUseCase,
        UpdateBotSettingsUseCase,
        RestartBotUseCase,
        ReportBotStatusUseCase,
        SetBotProxyUseCase,
        RemoveBotProxyUseCase,
        GetGraceSettingsUseCase,
        GetShopSettingsUseCase,
        GetCabinetSettingsUseCase,
        GetAccessSettingsUseCase,
        UpdateAccessSettingsUseCase,
        GetEmailSettingsUseCase,
        UpdateEmailSettingsUseCase,
        GetCabinetAuthSettingsUseCase,
        UpdateCabinetAuthSettingsUseCase,
        GetBotRequirementsUseCase,
        UpdateBotRequirementsUseCase,
        UpdateGraceSettingsUseCase,
        UpdateShopSettingsUseCase,
        GetOAuthSettingsUseCase,
        UpdateOAuthSettingsUseCase,
        GetBotCommandsUseCase,
        UpdateBotCommandsUseCase,
        GetPublicAppearanceUseCase,
        GetPublicAppearanceImageUseCase,
        UpdateAppearanceSettingsUseCase,
        ResetAppearanceSettingsUseCase,
        ResetBulkEmailSettingsUseCase,
        ResetEmailSettingsUseCase,
        UploadAppearanceLogoUseCase,
        UploadAppearanceFaviconUseCase,
        UploadAppearanceAppIconUseCase,
        DeleteAppearanceLogoUseCase,
        DeleteAppearanceFaviconUseCase,
        DeleteAppearanceAppIconUseCase,
    )
    bot_commands_sync = provide(BotCommandsPusher, provides=BotCommandsSync)


def get_settings_providers() -> list[Provider]:
    return [SettingsProvider()]
