# Roles & Permissions

## Rules

- **`core/enums.Role`: `USER < ADMIN < OWNER < SYSTEM`, ordered.** `OWNER` is singular, enforced by a DB partial unique index (see [db-models.md](db-models.md)), not just an app-level check. `SYSTEM` is the internal/background actor, never a real login.
- **`Permission` (`core/security/permissions.py`) is a flat `UpperStrEnum`**, named `RESOURCE_VERB` (`BANNERS_UPLOAD`, `LOCALES_ADD`, `BOT_COMMANDS_VIEW`, ...) — resource first, verb second, matching Remnawave's own `resource:action` scope convention and AWS IAM/GitHub OAuth scope ordering. Plus the special `PUBLIC` value that always passes regardless of actor.
- **`ROLE_PERMISSIONS: dict[Role, set[Permission]]` is the role default.** `ADMIN` is a deliberate strict subset — not the full set; `OWNER`/`SYSTEM` get every `Permission` (`set(Permission)`). Per-user overrides on top of this are built: `User.permission_grants`/`permission_revokes` (JSONB columns), granted/revoked by `OWNER` via `/admins*` endpoints, restricted to `GRANTABLE_PERMISSIONS`.
- **`PermissionPolicy.effective_permissions(actor)`** = `(ROLE_PERMISSIONS[actor.role] | grants) - revokes`. **`PermissionPolicy.has_permission(actor, permission)`**: `PUBLIC` always passes, otherwise membership check against `effective_permissions(actor)` (which is `set(Permission)` for `SYSTEM`/`OWNER` by role default, so they still always pass in practice). Both `grants`/`revokes` filter out any string that isn't a live `Permission` member (`if p in Permission`) — a renamed or retired permission just silently drops out of an actor's stored overrides, no migration needed when the enum changes.
- **Enforcement point is the usecase, not the router.** Every post-auth `UseCase` subclass sets `required_permission: Permission`; `UseCase._check_permissions()` runs before `_execute()`. A usecase with no `required_permission` set **denies by default** — see [usecases.md](usecases.md). For an API-token actor specifically, this is the *only* authorization boundary: `require_admin`'s role check passes any token (a `SyntheticActor`'s `is_privileged` is set to `True` explicitly at construction), so a wrong `required_permission` on a usecase is a real security bug, not just a UX inconvenience — see the granularity rule below for why every usecase needs its own test asserting this value.
- **`api/dependencies/admin.py`'s `require_admin` is a coarse routing-level gate** (role ≥ `ADMIN`), used to decide which router a request can even reach (`admin_router.py` vs `public_router.py`). It does not replace the fine-grained `Permission` check inside the usecase — both layers exist: "can this actor hit an admin endpoint at all" (router) and "does this actor have this specific capability" (usecase).

```python
class Permission(UpperStrEnum):
    PUBLIC = auto()
    BOT_SETTINGS_VIEW = auto()
    BOT_COMMANDS_VIEW = auto()
    BOT_SETTINGS_MANAGE = auto()
    BOT_COMMANDS_MANAGE = auto()
    BOT_RESTART = auto()
    OAUTH_SETTINGS_VIEW = auto()
    OAUTH_SETTINGS_MANAGE = auto()
    BANNERS_IMAGE_VIEW = auto()
    BANNERS_UPLOAD = auto()
    BANNERS_DELETE = auto()
    LOCALES_VIEW = auto()
    LOCALES_ADD = auto()
    LOCALES_REMOVE = auto()
    TRANSLATIONS_VIEW = auto()
    TRANSLATIONS_UPDATE = auto()
    TRANSLATIONS_RESET = auto()
    OWN_ACCOUNT_MANAGE = auto()
    ADMINS_MANAGE = auto()
    API_TOKENS_MANAGE = auto()
    EVENT_LOG_VIEW = auto()
    USERS_VIEW = auto()
    USERS_DETAIL_VIEW = auto()
    USERS_DELETE = auto()
    USERS_DISCOUNT_MANAGE = auto()
    USER_SESSIONS_VIEW = auto()
    USER_SESSIONS_REVOKE = auto()
    APPEARANCE_SETTINGS_MANAGE = auto()
    APPEARANCE_SETTINGS_RESET = auto()
    APPEARANCE_LOGO_UPLOAD = auto()
    APPEARANCE_LOGO_DELETE = auto()
    APPEARANCE_FAVICON_UPLOAD = auto()
    APPEARANCE_FAVICON_DELETE = auto()
    GATEWAYS_VIEW = auto()
    GATEWAYS_UPDATE = auto()
    GATEWAYS_ACTIVE_SET = auto()
    GATEWAYS_REORDER = auto()
    PLANS_VIEW = auto()
    PLANS_CREATE = auto()
    PLANS_UPDATE = auto()
    PLANS_DELETE = auto()
    PLANS_DURATIONS_MANAGE = auto()
    PLANS_ACCESS_MANAGE = auto()
    PLANS_SQUADS_MANAGE = auto()
    PLANS_ORDER_MANAGE = auto()
    SUBSCRIPTIONS_VIEW = auto()
    SUBSCRIPTIONS_UPDATE = auto()
    SUBSCRIPTIONS_SQUADS_MANAGE = auto()
    SUBSCRIPTIONS_DELETE = auto()
    ADDONS_VIEW = auto()
    ADDONS_CREATE = auto()
    ADDONS_UPDATE = auto()
    ADDONS_DELETE = auto()
    ADDONS_PRICES_MANAGE = auto()
    ADDONS_REORDER = auto()
    PROMOCODES_VIEW = auto()
    PROMOCODES_CREATE = auto()
    PROMOCODES_UPDATE = auto()
    PROMOCODES_DELETE = auto()
    PROMOCODES_GENERATE_CODE = auto()
    TRANSACTIONS_VIEW = auto()
    # ...about 250 members; core/security/permissions.py has the exact list

GRANTABLE_PERMISSIONS: Final[frozenset[Permission]] = frozenset(PERMISSION_METADATA)
# minus login/bot/gateway-credential changes, irreversible and bulk-overwrite actions
TOKEN_GRANTABLE_PERMISSIONS: Final[frozenset[Permission]] = (
    GRANTABLE_PERMISSIONS - _TOKEN_EXCLUDED_PERMISSIONS
)

# ADMIN's default is a strict subset; OWNER-only by default: admins/API tokens,
# appearance, backups, blacklist, event log, gateways, OAuth and email settings,
# user management (block/delete/merge/Telegram bind/sessions; viewing the users
# list and a user's card is in ADMIN's default), refunds, notifications,
# currency rates/anchor. Abridged here;
# ROLE_PERMISSIONS and test_role_permissions_admin_matches_expected_defaults pin
# the exact set.
ROLE_PERMISSIONS: Final[dict[Role, set[Permission]]] = {
    Role.SYSTEM: set(Permission),
    Role.OWNER: set(Permission),
    Role.ADMIN: {
        Permission.BOT_SETTINGS_VIEW,
        Permission.BOT_COMMANDS_VIEW,
        Permission.BOT_SETTINGS_MANAGE,
        Permission.BOT_COMMANDS_MANAGE,
        Permission.BOT_RESTART,
        # ... plus banners/locales/translations write permissions ...
        Permission.OWN_ACCOUNT_MANAGE,
        Permission.PLANS_VIEW,
        Permission.PLANS_CREATE,
        # ... plus the rest of plans/subscriptions/addons/promocodes ...
        Permission.USERS_VIEW,
        Permission.USERS_DETAIL_VIEW,
        Permission.USERS_DISCOUNT_MANAGE,
        Permission.TRANSACTIONS_VIEW,
    },
    Role.USER: set(),
}
```

## Granularity rule: one `Permission` per usecase, with named exceptions

**Default: every post-auth `UseCase` gets its own `Permission` member.** Two usecases must never share a `required_permission` value. This is a hard rule, not a case-by-case judgment call — a bundled permission means an `OWNER` can't grant one operation on a resource without also granting every other operation bundled under the same name, and an API token can't be scoped to less than the whole bundle. `Permission` is the actual authorization boundary for token actors (see the enforcement-point rule above), so under-granularity there is a real capability leak, not just an inconvenience.

**`PERMISSION_METADATA: dict[Permission, PermissionMeta]`** (`resource: str`, `kind: Literal["read", "write"]`) is the single source of truth for which permissions are *grantable at all* — `GRANTABLE_PERMISSIONS` is `frozenset(PERMISSION_METADATA)` and `TOKEN_GRANTABLE_PERMISSIONS` subtracts only the explicit `_TOKEN_EXCLUDED_PERMISSIONS` from it (the catalog's `token_grantable` flag is read from the result), never a separately maintained list, and it's also what `GET /permissions/catalog` returns verbatim (drives the frontend's `PermissionPicker`, grouped by `resource`). Adding a new `Permission` member without a `PERMISSION_METADATA` entry (and without adding it to the exceptions below) is a bug — `tests/core/security/test_permissions.py::test_every_catalog_permission_has_metadata` pins the exact set of non-catalog members, so forgetting one fails CI.

**Naming:** `RESOURCE_VERB`. Reading one entity by id is `RESOURCE_DETAIL_VIEW`, separate from the list's `RESOURCE_VIEW` (`USERS_DETAIL_VIEW`, `PLANS_DETAIL_VIEW`, `TRANSACTIONS_DETAIL_VIEW`, ...) and mirrored into `ADMIN`'s default wherever the list permission is. When a resource has a natural sub-resource with its own write permission already (e.g. `bot_settings` has both `BOT_SETTINGS_MANAGE` and `BOT_COMMANDS_MANAGE`), give the read side matching siblings too (`BOT_SETTINGS_VIEW` / `BOT_COMMANDS_VIEW`) instead of bundling both reads under one name — read and write sides of the same resource should have the same split shape.

**Exceptions — permissions that intentionally stay bundled across multiple usecases, and are never added to `PERMISSION_METADATA`:**

1. **Self-referential resources: `ADMINS_MANAGE`, `API_TOKENS_MANAGE`.** These gate an actor managing *other actors of the same kind* (admins managing admins, tokens managing tokens) — `OWNER`/`SYSTEM`-only forever, never token-grantable, so splitting them into per-usecase permissions buys nothing (nobody can ever be granted a subset). Confirmed against Remnawave's own backend (`ScopeCatalogService`, `libs/contracts`): their API-tokens and admin/role-management controllers carry no `@ScopeResource` decorator either — the same resources are structurally excluded from *their* token-scope catalog too. This is deliberate parity, not an oversight.
2. **`OWN_ACCOUNT_MANAGE`** — gates "the current actor operating on their own profile/sessions/OAuth identities/passkeys/notifications" across 25 usecases. This isn't a resource an actor CRUDs; it's "am I allowed to manage myself at all," which is binary by nature (an admin who can view their own sessions but can't revoke them isn't a distinction anyone has asked for) — neither admin- nor token-grantable: it's in every privileged role's default, and revoking it would break `GET /users/me`.
3. **`PUBLIC`** — always passes for any authenticated actor (~45 usecases). Most are customer self-service (checkout, plan catalog, promocode activation, referrals and bonus days, trial, devices, own transactions, notification feed, profile, ...) where the boundary is that the usecase acts on the actor's own identity (`get_current_user`/`get_cabinet_user`, see [AGENTS.md](../../AGENTS.md#topology)), not a capability to grant; plus a few reads every channel needs (`GetBannerForDisplayUseCase`, `GetCabinetSettingsUseCase`). The dashboard overview's `GetOverviewUseCase`, `GetRevenueChartUseCase`, `GetOnlineChartUseCase` and `GetAttentionUseCase` check each section's own read permission (`USERS_VIEW`, `SUBSCRIPTIONS_VIEW`, `TRANSACTIONS_VIEW`, `PLANS_VIEW`, ...) inside `_execute` instead; `GetHourlyOnlineUseCase` is open to any staff member on purpose.

**Not bundled, but still outside the catalog — `OWNER`/`SYSTEM`-only:** `PRICING_RATES_MANAGE`, `PRICING_ANCHOR_MANAGE`, `USERS_UNBLOCK_BLACKLIST_MATCHES`, `BACKUPS_RUN_DOWNLOAD` (a DATABASE dump subsumes every other read permission), `NOTIFICATION_VAPID_KEYS_GENERATE` (regenerating drops every device's push subscription), `OVERVIEW_RECAP_VIEW` (the shop recap card), `TRANSACTIONS_UNRATED_RATE` (rating transactions that lack an anchor amount). Each gates one usecase, but none is grantable to an admin or a token, and `ADMIN`'s role default doesn't include them. `tests/core/security/test_permissions.py` pins this set.

Everything else — every resource an admin or a scoped API token actually operates on (banners, locales, translations, bot settings, users, event log, ...) — follows the default rule: one permission per usecase, even when no concrete use case for the split has been named yet. A hand-authored enum that's *sometimes* coarse is a worse mental model than one that's always fine-grained except for the named, load-bearing exceptions above, and it matches Remnawave's per-endpoint token-scope granularity.

**Mechanism, deliberately not Remnawave's:** the catalog stays a hand-authored `Permission` enum + `PERMISSION_METADATA` dict, not an auto-discovered catalog built from route/usecase decorators (which is what Remnawave's `ScopeCatalogService` actually does, via NestJS's `DiscoveryService`/`Reflector`). `required_permission: Permission` being a real, `mypy`-checked enum member reference — not a runtime-validated string — is worth more than the boilerplate it costs. A typo in a decorator-driven scope string is a runtime bug; a typo referencing a `Permission` enum member that doesn't exist is a CI failure. This project has no reflection-based patterns anywhere else (Dishka is explicit DI, not scanning), and this isn't the place to start.
