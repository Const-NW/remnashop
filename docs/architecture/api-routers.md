# API Routers

## Rules

- **Split by audience, not by process.** `public_router.py` = "a valid session is required, role doesn't matter" (`get_current_actor`). `admin_router.py` = privileged (`require_admin`, role ≥ `ADMIN` — i.e. `ADMIN`/`OWNER`/`SYSTEM`, not literally `Role.ADMIN` only). Neither name means "no auth at all." This is a coarse routing-level gate, not the fine-grained capability check — see [permissions-roles.md](permissions-roles.md) for how `require_admin` and a usecase's `required_permission` relate. `public_router.py` exists in `transactions`, `subscriptions`, `promocodes`, `referrals`, `customer_notifications` and `notifications` — any session-required-but-not-admin route (e.g. a customer checking out their own subscription) goes here. Cabinet-only routes take `get_cabinet_user` (a `CABINET`-audience session); `legal` keeps those in `cabinet_router.py` (`/cabinet/legal`).
- **A route reachable with zero session doesn't belong in either file.** Bootstrap, login, status, and OAuth-**login** authorize/callback (signing in *via* a provider — distinct from OAuth-*connect*, which links a provider to an already-logged-in user's account and lives in `modules/oauth/api/admin_router.py`) structurally can't go through `get_current_actor` (no session exists yet to decode) — they get their own `router.py` in the module's `api/` package: `modules/auth/api/router.py`, `modules/cabinet_auth/api/router.py` (OAuth sign-in for the dashboard and the cabinet, `audience` field; cabinet Telegram/email sign-in), `modules/identity_links/api/router.py`, `modules/passkeys/api/router.py` (login flows), `modules/settings/api/router.py` (public appearance for the login page), `modules/emoji_packs/api/router.py` (public emoji media), `modules/legal/api/router.py` (`/legal/documents`), `modules/connect_guide/api/router.py` and `modules/users/api/router.py` (`POST /cabinet/unsubscribe`, HMAC-signed link), and `modules/transactions/api/router.py` (payment-gateway webhooks, `POST /payments/{gateway_type}`) and `modules/subscriptions/api/router.py` (Remnawave webhook) are the same shape for a server-to-server callback with no session to decode.
- **Resolve the usecase via `FromDishka[SomeUseCase]`** (dishka's FastAPI integration, `@inject`-decorated handler — see [di-providers.md](di-providers.md)) and call it with the actor and an input DTO built from the request schema (see [usecases.md](usecases.md)). Never resolve `UserRepository`/`UnitOfWork` directly in a router — go through the usecase.
- **Request/response Pydantic models live in the module's own `api/schemas.py`**, separate from the internal `dto.py`, and never return a DTO directly from a route. The response schema is the allowlist of what leaves the API: map with `XResponse.model_validate(dto, from_attributes=True)`, which reads only the schema's own fields (nested schemas included). Write a mapper by hand only where it transforms something — a computed field, a rename, a value the DTO doesn't carry.
- **Usually one `router = APIRouter(prefix="/<domain>", tags=["<domain>"])` per file**, but a file may define a second `APIRouter` for a closely related sub-resource instead of adding a new file. Privileged routes always stay in `admin_router.py` — `tests/api/test_admin_router_enforcement.py` checks `require_admin` only on routers it finds there — so `plans/api/admin_router.py` also holds `addons_router` (`/addons`) and `pricing_router` (`/pricing`), and `translations/api/admin_router.py` holds `locales_router` (`/settings/locales`) — `modules/users/api/admin_router.py` defines both `router` (`/users`) and `admins_router` (`/admins`). A prefix may also be shared across modules: `modules/auth/api/router.py` uses `/auth`, `modules/auth/api/admin_router.py`'s `sessions_router` uses `/auth/sessions` (the actor's own sessions) and its `user_sessions_router` uses `/users` (`/users/{user_id}/sessions` — another user's sessions sit under that user, like every other per-user sub-resource), and `modules/cabinet_auth/api/router.py` (`/auth`, sign-in at `/auth/oauth/{provider}/authorize`) shares `/auth/oauth` with `modules/oauth/api/admin_router.py` (connect).
- **Two layers of rate limiting.** `app/api/app.py` applies `global_rate_limit` (`api/dependencies/rate_limit.py`) as an app-level `dependencies=[...]`, so every route gets a 300/min baseline keyed by JWT `sub` when present, else client IP — except `/health`, `/readyz`, `/metrics`, and the Remnawave webhook path (`_RATE_LIMIT_EXEMPT_PATHS`; the webhook is excluded deliberately since every panel instance's calls share one IP bucket and the route already authenticates via HMAC signature verification, not because it needs no protection). `GET /emoji-packs/*` media gets its own 3000/min per-IP bucket, since a pack loads up to ~400 `<img>` files at once. On top of that baseline, a route opts into a *tighter*, purpose-specific limit: `rate_limit(key=..., limit=..., window_seconds=...)` (per IP), `user_rate_limit(...)` (per signed-in user) or `failure_rate_limit(...)` (counts only failed attempts). Used by every zero-session endpoint — `auth` (status 30/min, bootstrap 5/min, login 10/min, refresh/logout 30/min), `cabinet_auth` (OAuth authorize/callback 30/min plus 20/min failures, Telegram and email sign-in steps), `identity_links`, `passkeys` (options/authentication 10/min), `legal`, `connect_guide`, `/cabinet/unsubscribe` — and by a few authenticated customer routes where 300/min is far too loose: promocode activation and targets (10/min per user, code guessing), cabinet legal acceptance, push subscription, revoking other cabinet sessions.
- **Domain code never imports FastAPI.** Usecases raise typed exceptions from `core/exceptions.py` (`ConflictError`, `AuthenticationError`, ...), each with a mandatory `code=ErrorCode.X` (`core/error_codes.py`). `api/errors.py`'s global handler maps the class to a status (`ValidationError` 400, `AuthenticationError` 401, `PermissionDeniedError` 403, `NotFoundError` 404, `ConflictError` 409, `CooldownError` 429, `PaymentGatewayError` 502, `RemnawavePanelUnavailableError` 503) and answers `{"detail": <message>, "code": <ErrorCode>}` — routers don't catch-and-remap. Catch only to change the status (e.g. `AuthenticationError` on a route a logged-in admin calls must not be 401, or the frontend refreshes the session — re-raise as `ValidationError(str(error), code=error.code)`). An error with no domain exception behind it (CSRF, rate limit, webhook signature) is `api.errors.ApiError(status, detail, code=...)`. Every code needs an `apiErrors.<CODE>` entry in both frontend locales — `tests/api/test_errors.py` fails otherwise; the dashboard shows the translation and falls back to `detail`.
- **Document the errors a route can return** with `responses=error_responses(404, 409)` on the decorator. 401/403 are added automatically to every route that depends on `get_current_actor`, and 422/429 to every route (`GLOBAL_ERROR_RESPONSES`). `operationId` is the handler function name, so handler names must stay unique across routers.
- **URL shape** (mirrors Remnawave): CRUD on `/<resource>` and `/<resource>/{id}`; a non-CRUD action is `POST /<resource>/{id}/actions/<verb>` (or `POST /<resource>/actions/<verb>` for a collection-level one — `actions/reorder`, `actions/import`, `actions/generate-code`, `pricing/actions/switch-anchor`); a bulk operation is `POST /<resource>/bulk/<verb>`. Protocol steps stay bare verbs, as in Remnawave (`auth/login`, OAuth `authorize`/`callback`, passkey `options`/`verify`). Resetting to defaults is `POST .../actions/reset` returning the new state (`settings/appearance/{target}/actions/reset`, `backups/settings/actions/reset`; `banners/actions/reset-default` restores only the shared default banner) — never `DELETE`, the resource still exists afterwards. Sub-resources of one user live under `/users/{user_id}/...`. Payment/Remnawave webhook URLs are registered in external systems — never rename them.
- **Methods & statuses.** Creating a new entity is `POST` → 201 (`api-tokens`, `blacklist/sources`, `checkout`, `promocodes/activations`). Something with a known address is an idempotent `PUT` to that address, paired with `DELETE` on the same path: uploads (`PUT /banners/{name}/{locale}`, `PUT /settings/appearance/{target}/logo`), set membership (`PUT /settings/locales/{locale}` — re-adding is a no-op, `PUT /blacklist/entries/{kind}/{value}`). Never a `DELETE` with a body — proxies and clients drop it. Partial updates (all-optional body, omitted = unchanged) are `PATCH`, and a plain flag (`is_active`, `enabled`) is just a `PATCH` field (plans, addons, promocodes, blacklist sources). Two explicit actions `actions/enable` / `actions/disable` only when switching on has a side effect or a check (gateways: must be configured), never a `toggle` — a retried or double-clicked request must not flip the state back. `PUT` also replaces a whole value (`/override/{currency}` where `null` clears, `/squads`, `/bot/commands`).
- **Response shape.** Never a top-level array: a collection is `{items, total}` — one `XListResponse` model per endpoint in the module's `schemas.py`, paginated ones add `page`/`page_size` query params; `actions/reorder` returns the same shape as its list; a settings value is an object (`{commands}`, `{lines}`), and GET/PUT on one path share one model (`UpdateBotCommandsResponse` extends `BotCommandsResponse`). An object can grow a field without breaking clients; an array can't. The frontend unwraps in `entities/*/api.ts` (`shared/api/http.ts`'s `ListResponse<T>`), so components keep working with `T[]`.
- **`GET /{id}` for every catalog entity an admin opens one at a time** (users, plans, promocodes, subscriptions, addons, transactions, ad links, blacklist sources), returning the same shape as one list item and gated by its own `*_DETAIL_VIEW` permission. Own-account sub-resources (sessions, passkeys, API tokens) and dialog windows are list-only by design. Numeric path params use the `{id:int}` convertor (`@router.get("/{user_id:int}")`) so a sibling static route in another router (`/users/me`, `/users/stats`, `/plans/catalog`, `/promocodes/summary`) can't be shadowed by it; OpenAPI still shows `{user_id}`.
- **Date filters** are `<column>_from` / `<column>_to`, named after the column they filter (`created_from`, `occurred_from`), half-open `[from, to)` compared with `>=` / `<`, and wherever `_from` exists `_to` exists too. A half-open range lets adjacent periods (the summary's "previous period") meet without overlap or a gap. A bare date passed as `_to` means the whole day: `core.utils.time.range_end_utc` turns it into the next midnight.

## Example

```python
router = APIRouter(prefix="/settings", tags=["settings"])

@router.patch("/bot", response_model=BotSettingsResponse)
@inject
async def update_bot_settings(
    payload: UpdateBotSettingsRequest,
    use_case: FromDishka[UpdateBotSettingsUseCase],
    actor: Actor = Depends(require_admin),
) -> BotSettingsResponse:
    settings = await use_case(
        actor,
        UpdateBotSettingsDto(
            token=payload.token, secret_token=payload.secret_token,
            drop_pending_updates=payload.drop_pending_updates,
        ),
    )
    return BotSettingsResponse.model_validate(settings, from_attributes=True)
```

Pre-auth exception (`modules/auth/api/router.py`):

```python
@router.post(
    "/bootstrap",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(forbid_cabinet_proxy),
        Depends(rate_limit(key="bootstrap", limit=5, window_seconds=TIME_1M)),
    ],
    responses=error_responses(409),
)
@inject
async def bootstrap(
    payload: BootstrapRequest,
    request: Request,
    response: Response,
    use_case: FromDishka[BootstrapOwnerUseCase],
    config: FromDishka[AppConfig],
) -> AuthResponse:
    # ConflictError -> 409 {"detail", "code"} via the global handler
    owner, issued = await use_case.execute(
        login=payload.login,
        password=payload.password,
        client_ip=resolve_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    set_session_cookies(response, issued, config.secure_cookies)
    return to_auth_response(owner, issued.access_token)
```
