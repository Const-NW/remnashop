# Usecases

Two patterns, both living in the same `usecases/` package. The split is **whether the class subclasses `core.usecase.UseCase`** — not naming, not folder placement.

Clean Architecture uses "Interactor" and "Use Case" interchangeably; this project uses only `*UseCase`, for both patterns.

## Pattern 1 — post-authentication (the common case)

Subclass `core.usecase.UseCase[InputDto, OutputDto]`:

- Set `required_permission: Permission` as a class attribute.
- Implement `async def _execute(self, actor: Actor, data: InputDto) -> OutputDto`. `core.actor.Actor` covers both a session's `UserDto` and an API token's `SyntheticActor`; a usecase acting on the caller's own account (checkout, trial, devices, catalog, promocode activation) declares `UseCase[InputDto, OutputDto, UserDto]` instead, so mypy refuses a token actor and its route resolves the actor through `get_current_user`.
- The base class's `__call__(actor, data)` runs `_check_permissions(actor)` before `_execute` — the actor is checked against `required_permission`, not assumed valid. See [permissions-roles.md](permissions-roles.md).
- A usecase with no `required_permission` set **denies by default** (fails closed), it does not silently allow.
- `.system` gives a `BoundUseCase` pre-bound to a synthetic system actor (`core.actor.SYSTEM_ACTOR`), for background/internal callers that have no real actor and skip the permission check entirely.
- `modules/oauth/`'s four OAuth-**connect** classes (`OAuthConnectAuthorizeUseCase`, `OAuthConnectCallbackUseCase`, `OAuthDisconnectUseCase`, `ListOAuthIdentitiesUseCase` — linking/unlinking a provider to an *already-logged-in* user's account, `required_permission = Permission.OWN_ACCOUNT_MANAGE`) are this pattern: they subclass `UseCase` and take a real `actor`. Don't confuse them with the OAuth-**login** classes below, which are a different flow (no existing session) and genuinely pattern 2.

```python
class UpdateBotSettingsUseCase(UseCase[UpdateBotSettingsDto, BotSettingsDto]):
    required_permission = Permission.BOT_SETTINGS_MANAGE

    def __init__(
        self,
        settings_repository: SettingsRepository,
        uow: UnitOfWork,
        event_publisher: EventPublisher,
    ) -> None:
        self._settings_repository = settings_repository
        self._uow = uow
        self._event_publisher = event_publisher

    async def _execute(self, actor: Actor, data: UpdateBotSettingsDto) -> BotSettingsDto:
        async with self._uow:
            before = await self._settings_repository.get_bot_settings()
            settings = await self._settings_repository.update_bot_settings(data)
            # audit event in the same transaction as the write
            await self._event_publisher.publish(
                BotSettingsChangedEvent(
                    actor=actor,
                    action="update",
                    changed_fields=changed_fields(before, settings, vars(data)),
                )
            )
        logger.info(f"{actor.log} Updated bot settings")
        return settings
```

## Pattern 2 — pre-authentication

Plain DI-injected classes with an `execute()` method, no `UseCase` base, no actor parameter:

- `modules/auth/`: bootstrap, login, status, refresh, logout.
- `modules/oauth/`: OAuth-**login** authorize/callback for the dashboard (`OAuthLoginAuthorizeUseCase`/`OAuthLoginCallbackUseCase` — signing in *via* a provider, no session exists yet), served from `cabinet_auth`'s router next to the cabinet's own `CabinetOAuthAuthorizeUseCase`/`CabinetOAuthCallbackUseCase`.
- `modules/cabinet_auth/`: cabinet sign-in steps (Mini App `TelegramWebAppLoginUseCase`, bot deeplink, email codes, `GetCabinetMethodsUseCase`).
- `modules/passkeys/`: passkey login (`GetPasskeyAuthenticationOptionsUseCase`, `VerifyPasskeyAuthenticationUseCase`).
- `modules/settings/`: `GetPublicAppearanceUseCase`/`GetPublicAppearanceImageUseCase` — the logo/favicon on the pre-login page.
- `modules/emoji_packs/`: public emoji thumbnails/animations (`/emoji-packs/*`), fetched as plain `<img>` sources that can't carry a bearer token.
- `modules/legal/` (`ListVisibleLegalDocumentsUseCase`), `modules/connect_guide/` (`GetPublicConnectGuideUseCase`), `modules/users/` (`UnsubscribeMarketingUseCase`, an HMAC-signed link): zero-session reads and writes.

The same plain shape serves cabinet-session usecases that take the signed-in customer as `user: UserDto` (from `get_cabinet_user`) rather than an actor with permissions: `modules/identity_links/` (link/unlink identities, confirm a merge) and `legal`'s pending/accept documents.

There is structurally no actor yet — that's what these usecases produce. Don't force them into `UseCase` with a placeholder actor.

```python
class BootstrapOwnerUseCase:
    def __init__(
        self, user_repository: UserRepository, settings_repository: SettingsRepository,
        uow: UnitOfWork, config: AppConfig, event_publisher: EventPublisher,
        session_issuer: SessionIssuer,
    ) -> None: ...

    async def execute(
        self, login: str, password: str, client_ip: str, user_agent: Optional[str]
    ) -> tuple[UserDto, IssuedSession]: ...
```

## Rules that apply to both patterns

- **Dependencies come through `__init__`** (`UserRepository`, `UnitOfWork`, `AppConfig`, `EventPublisher`, ...), registered as dishka providers in the module's `di.py`. See [di-providers.md](di-providers.md).
- **Any usecase that writes must wrap the write in `async with self._uow: ...`.** `UnitOfWork.__aexit__` commits automatically on a clean exit and rolls back on exception; new code doesn't call `await self._uow.commit()` inside the block. A repository only flushes (see [repositories.md](repositories.md)), so a write outside a unit of work is lost when the session closes, and a same-transaction unit test can't tell flush from commit.
- **Publish domain events instead of calling `logger.*` directly** for anything a subscriber might care about (failed login, bootstrap conflict, ...) — via the injected `EventPublisher` port. See [events.md](events.md).
- **One file per usecase** in `usecases/`, re-exported from `usecases/__init__.py` — what routers, tasks and `di.py` import. The module's top-level `__init__.py` (the only thing other modules may import) exports only the names some other package actually imports (`tests/test_module_interfaces.py`).
- **Every usecase input and output type is a `*Dto` in the module's `dto.py`** — never declared inside a `usecases/` file, never `*Input`/`*Result`. A `usecases/` file holds the usecase and its private helpers only. Where a usecase input would collide with a repository-level DTO of the same name, the usecase one takes a verb that says what it does (`IssueApiTokenDto` next to the repository's `CreateApiTokenDto`, `UploadBannerFileDto` next to `UploadBannerDto`).
- **Two-tier DTO model**: a usecase-input `Dto` may carry fields the repository never sees (`client_ip`, used only for event publishing). It maps explicitly, field by field, into a repository-level Create/Update `Dto` — the exact set of mutable columns being written. When the two coincide exactly (no extra usecase-only fields, no usecase-computed values), one class serves both layers instead of introducing a duplicate — see `modules/settings`'s `UpdateBotSettingsDto`, defined once in `dto.py` and used unchanged by both the usecase and the repository.
- **Input DTOs for partial updates**: `@dataclass(frozen=True, kw_only=True)` with `Optional[...] = None` fields — the usecase/repository only applies fields that are not `None`, so callers can omit anything they're not changing.
- **Role/state-transition operations get dedicated repository methods, not the generic partial-update DTO.** `modules/users`' `promote_to_admin`/`demote_from_admin` are the example: `demote_from_admin` needs to *clear* `login`/`password_hash`/`permission_grants`/`permission_revokes`/`ip_allowlist` to `None`, which is the opposite of what `None` means in a partial-update DTO ("leave unchanged"). Forcing an atomic, fully-specified state change through the same optional-fields DTO makes "clear this field" inexpressible without a sentinel type. A usecase whose job is "transition to a specific state," not "apply whatever subset of fields the caller specified," gets its own repository method with a fixed signature instead.
