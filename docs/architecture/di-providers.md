# DI / Providers (Dishka)

## Rules

- **Each module owns a `di.py`**: one `Provider` subclass (`scope = Scope.REQUEST`) plus a `get_<module>_providers() -> list[Provider]` factory, which the module's top-level `__init__.py` re-exports for `app/modules/providers.py`. For what the classes being registered here actually look like, see [repositories.md](repositories.md) and [usecases.md](usecases.md).
- **`provide_all(...)`** for everything whose `__init__` params are all themselves resolvable by the container — one `use_cases = provide_all(...)` group and one `repositories`/`services` group per provider. A lone class keeps `x = provide(X)`, and anything with `scope=`, `provides=` or an `alias(...)` stays a separate explicit line (a port bound to its implementation: `bot_commands_sync = provide(BotCommandsPusher, provides=BotCommandsSync)`).
- **`@provide`-decorated method** only when construction needs logic beyond calling the constructor — in practice a task-backed `*Dispatch` port, `Scope.APP`, the taskiq task cast to the port:

```python
class SettingsProvider(Provider):
    scope = Scope.REQUEST

    settings_repository = provide(SettingsRepository)
    bot_commands_sync = provide(BotCommandsPusher, provides=BotCommandsSync)
    use_cases = provide_all(
        GetBotSettingsUseCase,
        UpdateBotSettingsUseCase,
        # ...
    )

class OAuthDIProvider(Provider):  # modules/oauth/di.py
    scope = Scope.REQUEST

    oauth_identity_repository = provide(OAuthIdentityRepository)
    oauth_state_store = provide(OAuthStateStore)
    oidc_identity_cleaner = alias(source=OAuthIdentityRepository, provides=OidcIdentityCleaner)

class BackupsProvider(Provider):  # modules/backups/di.py
    @provide(scope=Scope.APP)
    def run_backup_dispatch(self) -> RunBackupDispatch:
        return cast(RunBackupDispatch, run_backup_task)
```

- **Every process gets the whole graph**: `app/modules/providers.py`'s `get_module_providers()` lists every module's `get_<module>_providers()`, and the `api`, `worker` and `telegram` containers all build from it plus `get_core_providers()`. A new module adds one line there. Usecases are request-scoped and built on demand, and dishka validates the full graph once at container build; `tests/worker/test_container.py` resolves every task's `FromDishka` dependency.
- **Core infra providers** (`AsyncEngine`, session maker, `AsyncSession`, `UnitOfWork`, `Redis` and the `BotSignals` port on it, `EventBus`, `AppConfig`, the Remnawave client) live under `core/di/providers/` as their own package (`config.py`, `database.py`, `event_bus.py`, `redis.py`, `remnawave.py`). `Scope.APP` for process-lifetime singletons (engine, session maker, event bus); `Scope.REQUEST` for per-request resources (`AsyncSession`, `UnitOfWork`).
- **`FromDishka[...]` only appears at the router/handler boundary** — a FastAPI route parameter (`use_case: FromDishka[SomeUseCase]`) or an aiogram handler parameter, decorated with `@inject`. Never resolve `UserRepository`/`UnitOfWork` in a router and pass them through by hand; the usecase gets its own dependencies through its `__init__`.
- **A single `provide()` attribute is the snake_case name of the class it provides** (`settings_repository = provide(SettingsRepository)`); `provide_all` groups are named for what they hold (`use_cases`, `repositories`, `services`).
