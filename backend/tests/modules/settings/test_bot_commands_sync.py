from typing import Optional

import pytest

from app.core.db.uow import UnitOfWork
from app.core.enums import Locale
from app.modules.settings import bot_commands_sync
from app.modules.settings.command_translations import CommandMenu, push_bot_commands_to_telegram
from app.modules.settings.dto import UpdateBotSettingsDto
from app.modules.settings.repository import SettingsRepository
from app.modules.translations.repository import TranslationRepository
from tests.fakes.telegram_bot import FakeBotSession, use_fake_bot
from tests.modules.settings.factories import make_bot_commands_sync


async def _setup(
    settings_repository: SettingsRepository, translation_repository: TranslationRepository
) -> None:
    await translation_repository.add_active_locale(Locale.EN)
    await translation_repository.seed_missing(Locale.EN, "command", "start", "Open the main menu")
    await translation_repository.seed_missing(Locale.EN, "command", "paysupport", "Refunds")
    await translation_repository.seed_missing(Locale.EN, "command", "terms", "Terms")
    await settings_repository.update_bot_settings(
        UpdateBotSettingsDto(token="123:abcdef", secret_token="webhook-secret")
    )


async def test_an_unchanged_menu_is_not_resent(
    monkeypatch: pytest.MonkeyPatch,
    settings_repository: SettingsRepository,
    translation_repository: TranslationRepository,
    uow: UnitOfWork,
) -> None:
    await _setup(settings_repository, translation_repository)
    bot = use_fake_bot(
        monkeypatch, FakeBotSession({"setMyCommands": True, "deleteMyCommands": True})
    )
    sync = make_bot_commands_sync(settings_repository.session, uow)

    assert await sync.push()
    sent = len(bot.calls)
    assert sent > 0

    assert await sync.push()
    assert len(bot.calls) == sent

    assert await sync.push(force=True)
    assert len(bot.calls) == 2 * sent


async def test_a_changed_description_is_resent(
    monkeypatch: pytest.MonkeyPatch,
    settings_repository: SettingsRepository,
    translation_repository: TranslationRepository,
    uow: UnitOfWork,
) -> None:
    await _setup(settings_repository, translation_repository)
    bot = use_fake_bot(
        monkeypatch, FakeBotSession({"setMyCommands": True, "deleteMyCommands": True})
    )
    sync = make_bot_commands_sync(settings_repository.session, uow)
    await sync.push()

    await translation_repository.update_value(Locale.EN, "command", "start", "Menu")
    await sync.push()

    assert bot.called("setMyCommands")[-1]["commands"][0]["description"] == "Menu"


async def test_a_refused_push_is_retried_next_time(
    monkeypatch: pytest.MonkeyPatch,
    settings_repository: SettingsRepository,
    translation_repository: TranslationRepository,
    uow: UnitOfWork,
) -> None:
    await _setup(settings_repository, translation_repository)
    bot = use_fake_bot(
        monkeypatch, FakeBotSession({"setMyCommands": False, "deleteMyCommands": True})
    )
    sync = make_bot_commands_sync(settings_repository.session, uow)

    assert not await sync.push()
    bot.replies["setMyCommands"] = True
    sent = len(bot.calls)
    assert await sync.push()
    assert len(bot.calls) > sent


async def test_a_dropped_locale_gets_its_menu_again(
    monkeypatch: pytest.MonkeyPatch,
    settings_repository: SettingsRepository,
    translation_repository: TranslationRepository,
    uow: UnitOfWork,
) -> None:
    await _setup(settings_repository, translation_repository)
    bot = use_fake_bot(
        monkeypatch, FakeBotSession({"setMyCommands": True, "deleteMyCommands": True})
    )
    sync = make_bot_commands_sync(settings_repository.session, uow)
    await sync.push()

    await sync.drop_locale(Locale.EN)
    sent = len(bot.calls)
    await sync.push()

    assert len(bot.calls) > sent


async def test_a_half_sent_menu_is_resent_even_when_texts_go_back(
    monkeypatch: pytest.MonkeyPatch,
    settings_repository: SettingsRepository,
    translation_repository: TranslationRepository,
    uow: UnitOfWork,
) -> None:
    await _setup(settings_repository, translation_repository)
    bot = use_fake_bot(
        monkeypatch, FakeBotSession({"setMyCommands": True, "deleteMyCommands": True})
    )
    sync = make_bot_commands_sync(settings_repository.session, uow)
    await sync.push()

    # the new menu reaches the EN language, then the fallback one is refused
    await translation_repository.update_value(Locale.EN, "command", "start", "Menu")
    bot.replies["setMyCommands"] = lambda params: params.get("language_code") is not None
    assert not await sync.push()

    await translation_repository.update_value(Locale.EN, "command", "start", "Open the main menu")
    bot.replies["setMyCommands"] = True
    sent = len(bot.calls)
    assert await sync.push()

    assert len(bot.calls) > sent
    assert bot.called("setMyCommands")[-1]["commands"][0]["description"] == "Open the main menu"


async def test_an_edit_during_a_push_is_sent_by_the_running_push(
    monkeypatch: pytest.MonkeyPatch,
    settings_repository: SettingsRepository,
    translation_repository: TranslationRepository,
    uow: UnitOfWork,
) -> None:
    await _setup(settings_repository, translation_repository)
    bot = use_fake_bot(
        monkeypatch, FakeBotSession({"setMyCommands": True, "deleteMyCommands": True})
    )
    sync = make_bot_commands_sync(settings_repository.session, uow)
    edited = False

    async def edit_while_sending(token: str, proxy: Optional[str], menu: CommandMenu) -> bool:
        nonlocal edited
        if not edited:
            edited = True
            await translation_repository.update_value(Locale.EN, "command", "start", "Menu")
            sent = len(bot.calls)
            # the lock is taken: the second push hands its menu to this one
            assert await sync.push()
            assert len(bot.calls) == sent
        return await push_bot_commands_to_telegram(token, proxy, menu)

    monkeypatch.setattr(bot_commands_sync, "push_bot_commands_to_telegram", edit_while_sending)

    assert await sync.push()

    assert bot.called("setMyCommands")[-1]["commands"][0]["description"] == "Menu"
