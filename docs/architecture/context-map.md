# Context Map

What each module in `backend/app/modules/*` owns, and how they actually depend on each other (from cross-module imports, not just what import-linter *permits*). See [Topology](../../AGENTS.md#topology) for the four-process split (`api`/`telegram`/`worker`/`cli`) that runs these modules, and [permissions-roles.md](permissions-roles.md) for the actor model referenced throughout.

## Modules

| Module | Type | Owns |
|---|---|---|
| `access` | Supporting | Registration access mode — registration check and `is_restricted_for` enforcement per channel; `CheckRegistrationAccessUseCase` used by api/telegram dependencies |
| `auth` | Generic | Login/password, bootstrap of the single `OWNER`, sessions + refresh-token rotation, theft detection |
| `oauth` | Generic | OAuth provider adapters (GitHub/OIDC/Telegram/Google/Yandex, `oauth/providers/`), state store, identity linking, the dashboard's OAuth-connect routes; sign-in through a provider is served by `cabinet_auth` |
| `passkeys` | Generic | WebAuthn passkey login/register — discoverable credentials, usernameless authentication |
| `users` | Generic | Accounts (customers + admins), roles, per-user permission grants/revokes, IP allowlist — the actor every other module authorizes against |
| `settings` | Supporting | Single-row bot config (token, proxy, OAuth client secrets, appearance, shop settings), encrypted at rest; the bot's command menu and its push to Telegram (`BotCommandsPusher`, the `BotCommandsSync` port) |
| `event_log` | Supporting | Audit trail — also relays *every* published event off the transactional outbox to in-process `EventBus` subscribers, from the `worker` process |
| `banners` | Supporting | Marketing image/banner content served to the bot |
| `api_tokens` | Supporting | Scoped API tokens for programmatic dashboard access |
| `translations` | Supporting | Fluent (`.ftl`) translation entries per locale, DB-editable, and the set of active locales (`/settings/locales` routes live here) |
| `gateways` | Supporting | Payment gateway configuration (which providers are enabled, field-level settings) |
| `blacklist` | Supporting | Telegram-id/email ban list, entries added manually or synced from external source lists |
| `backups` | Generic | Postgres backup/restore — `pg_dump`/`pg_restore` wrapper with provenance verification, a solved ops problem rather than a product differentiator |
| `advertising` | Supporting | Ad-link tracking — attribution codes, per-link conversion stats |
| `pricing` | Core | FX pricing engine: currency rates, pricing settings (anchor currency), `price_for`/`resolve_price`/`round_amount`. Also the single home of `convert_for_report`, the display-only FX conversion every report goes through (analytics, overview, advertising, payment/promocode/referral summaries, referral percent rewards). Knows nothing about plans or add-ons |
| `plans` | Core | The sellable catalog: plans with durations and squad access, add-ons, their base prices and per-currency overrides, and the price book over both (`/pricing` admin routes, anchor switch, base-price adjustment, change preview) |
| `subscriptions` | Core | Multi-subscription orchestration: platform user → Remnawave VPN subscription, add-on entitlements, lifecycle/renewal, admin management (free grant, plan change, expiry, status, traffic reset, link reissue, devices), the customer-facing catalog (`GET /plans/catalog`) and `PurchaseEligibility` (concurrent-subscription cap, plan availability) |
| `promocodes` | Supporting | Discount codes, redemption |
| `referrals` | Supporting | Two-level referral program — attribution, points ledger, reward payout (including granting reward subscriptions) |
| `transactions` | Core | Checkout orchestration — turns a plan + gateway selection into a subscription purchase; the gateway adapters (`transactions/gateways/`), their webhooks and admin test payments |
| `cabinet_auth` | Generic | Customer sign-in for the web cabinet (`/cabinet/*`): Mini App `initData`, bot deeplink, Telegram/Google/Yandex OAuth, email codes; also OAuth sign-in for the dashboard (`audience` field); `CABINET`-audience sessions |
| `identity_links` | Supporting | A signed-in customer links Telegram, OAuth or an email to their own account; a key owned by another account leads to a confirmed merge through `account_merge` |
| `account_merge` | Supporting | Admin merge of two user accounts: per-conflict choices, child rows moved by one repository; publishes `AccountsMergedEvent` |
| `email` | Generic | SMTP sending from `worker` (sign-in codes, test email), optional bulk SMTP profile and per-minute rate for broadcasts |
| `notifications` | Supporting | Admin notifications: groups, per-admin group × channel matrix, web push subscriptions and VAPID keys, delivery by push and Telegram |
| `customer_notifications` | Supporting | Transactional messages to customers about their own subscription, payments, referral rewards, new-device sign-ins; Telegram, email or both per event; cabinet notification feed |
| `broadcasts` | Supporting | Customer broadcasts: audience filters, Telegram/email delivery, per-locale content, paced sending from `worker`, recall |
| `loyalty` | Supporting | Automatic loyalty discount tiers, per-user exclusion, recalculated from `worker` |
| `announcements` | Supporting | Maintainer announcements synced from the public GitHub feed |
| `connect_guide` | Supporting | Dashboard-edited connection guide the cabinet reads through a zero-session route |
| `analytics` | Supporting | Reporting — KPI (revenue, MRR, ARPU, conversion), churn/retention, funnel, cohorts, LTV distribution |
| `overview` | Supporting | Dashboard home — one aggregate read over users/subscriptions/transactions/online/runtime, each section gated by the actor's own permissions |
| `dialogs` | Supporting | Bot dialog windows and buttons editable from the dashboard, custom windows, export/import — rendered by `app/telegram/dialogs/` |
| `emoji_packs` | Supporting | Telegram custom-emoji packs for the dialog editor — pack catalog, thumbnail/animation media |
| `legal` | Supporting | Legal documents (offer, user agreement, privacy policy): per-channel visibility flags, append-only per-locale revisions with optional BOT/CABINET override, the append-only acceptance journal (`LegalAcceptanceRecorder`, written by `transactions` at checkout and `cabinet_auth` at the first cabinet sign-in), public `/legal/documents` for the cabinet |

Core/Supporting/Generic is a DDD strategic classification, not enforced anywhere in code — it's a judgment call to help prioritize where investment matters most (Core = differentiates the product; Supporting = necessary but replaceable; Generic = solved problem, not worth innovating on).

## Dependency graph

**`users` is the universal dependency**: a usecase acting on the caller's own account narrows its actor to `UserDto` ([usecases.md](usecases.md)), and almost every module reads or writes the `User` row through `UserRepository`; `analytics` and `overview` also read `UserStatsRepository`. Below, `users` is left out of each line unless it is the module's only edge.

Real cross-module edges (`users` omitted), all through the target module's public `__init__.py`, from grepping `app.modules.*` imports:

```
customer_notifications -> account_merge, cabinet_auth, email, notifications, promocodes, referrals, settings, subscriptions, transactions
overview      -> api_tokens, backups, broadcasts, email, event_log, notifications, plans, pricing, settings, subscriptions, transactions
cabinet_auth  -> access, auth, blacklist, email, legal, oauth, settings
identity_links -> account_merge, auth, blacklist, cabinet_auth, email, oauth, settings
analytics     -> advertising, pricing, promocodes, referrals, subscriptions, transactions
transactions  -> gateways, legal, plans, pricing, settings, subscriptions
referrals     -> plans, pricing, settings, subscriptions, transactions
loyalty       -> account_merge, plans, pricing, subscriptions, transactions
broadcasts    -> email, promocodes, settings, subscriptions, translations
promocodes    -> plans, pricing, subscriptions, transactions
subscriptions -> plans, pricing, settings
advertising   -> pricing, settings, transactions
access        -> advertising, settings
oauth         -> auth, blacklist, settings
passkeys      -> auth, settings
notifications -> promocodes, settings, subscriptions, transactions, translations
plans         -> pricing, settings
dialogs       -> banners, translations
auth, email   -> settings
settings      -> translations
banners       -> translations
legal         -> translations
emoji_packs   -> settings
event_log     -> notifications
users         -> blacklist
account_merge, announcements, gateways -> users
api_tokens, backups, blacklist, connect_guide, pricing, translations -> (nothing)
```

**There are no cycles.** The commerce core is layered `pricing → plans → subscriptions → transactions → referrals/promocodes`, each module importing only the ones to its left. Where a lower module needs something a higher one owns, it declares a port in its own `ports.py` (exported from `__init__.py`) and the higher module implements it through DI — e.g. `users.SessionRevoker`/`SubscriptionRevoker`/`PasskeyRevoker`/`OAuthIdentityLookup`/`SignInMethodCheck`/`PromocodeLiveness`, `auth.PasskeyCounter`, `plans.PlanReferenceCleaner`/`AnchorAmountRescaler`/`LoyaltyThresholdRescaler`, `subscriptions.ReferrerLookup`/`SpentPurchaseDiscount`/`OrderFailer`/`OpenOrderCounter`, `transactions.PromocodeLiveness`/`PaymentsRestoredNotifier`, `settings.OidcIdentityCleaner`/`AudienceSessionRevoker`, `translations.DefaultBannerSeed`/`BotCommandsSync`. `core/ports.py` keeps only infrastructure ports (`EventPublisher`, `OutboxEntryDispatch`, `BotSignals`, the translator protocols). The less obvious ones:

- `ReferrerLookup` — `subscriptions`' `PurchaseEligibility` asks whether a user was invited (INVITED plans); `referrals` aliases its `ReferralRepository` to it.
- `BotCommandsSync` — `translations` re-pushes the bot's command menu after an edit or a locale change; `settings` implements it with `BotCommandsPusher`, since only it holds the bot token.
- `BotSignals` — dialogs, translations and settings tell a running bot process to reload or restart; `core/bot_signals.py` implements it over Redis pub/sub and `app/telegram/bot_signals.py` listens.

`backend/.importlinter`'s `modules-acyclic` contract (`acyclic_siblings` over `app.modules`) fails the build on any new cycle, so a two-way need between modules has to become a port or an event, not an import back.

`convert_for_report` (`pricing/resolver.py`) is the one report-side currency conversion: amount → anchor → target, cut to cents (`ROUND_HALF_UP`), no rounding mode or charm ending — those are checkout rules, `resolve_price`'s job. A new report converts through it, not a local helper. Charged and listed prices go through `price_for` (a duration's or add-on's pinned price, else its converted base) — the catalog and the checkout quote share it.

`referrals`' edge into `subscriptions` is a real usecase call, not just a repo read: `AssignReferralRewardsUseCase` runs `PurchaseSubscriptionUseCase` to grant reward days.

**Event-driven decoupling, not import** (handlers in each module's `subscribers.py`, registered by `app/worker/subscribers.py`): `advertising` reacts to `TelegramUserRegisteredEvent`/`CabinetUserRegisteredEvent` for attribution; `referrals` to the same two plus `SubscriptionPurchasedEvent`/`SubscriptionRenewedEvent`/`SubscriptionChangedEvent`/`TransactionRefundedEvent` for rewards and their reversal; `loyalty` to purchases, renewals, plan changes, refunds and `AccountsMergedEvent` for recalculation; `transactions` to purchases/renewals/changes (marking a paid order fulfilled) and `AccessSettingsChangedEvent` (notifying the payment waitlist); `customer_notifications` to every customer-facing event; `notifications` subscribes by stored event type (`subscribe_stored`) to deliver grouped events to admins. The publishing modules never import their consumers — the modules only share the event class. `translations`' `LocaleActivatedEvent`/`LocaleDeactivatedEvent` have no subscriber (audit only): `add_locale` copies `en` translations into the new locale directly in its own transaction, since the dashboard re-reads them right after and an async copy lost that race.

Every publish goes through a transactional outbox, not straight to in-process subscribers — `core/outbox_event_publisher.py`'s `OutboxEventPublisher` (bound to the `EventPublisher` port every usecase injects) writes an `EventOutboxEntry` row in the *same* DB transaction as the usecase and kicks the worker after commit. `event_log`'s `process_outbox_entry_task`/`sweep_outbox_task` (running in the `worker` process) pick the row up, persist it permanently to the audit log, and call `bus.dispatch_stored()` to run any type-specific `EventBus` subscriber (e.g. auth's per-event logging) — so `event_log` sits on the delivery path for every event in the system, not just as a passive listener. See [events.md](events.md).

**Boundary enforcement:** `backend/.importlinter`'s `module-boundaries` contract (custom type `ModuleBoundariesContract` in `backend/import_contracts.py`) covers every child of `app.modules` automatically: everything under `app.modules.<x>.*` is importable only from the module itself and from the two composition roots that wire it (`app.api.routers`, `app.worker.subscribers`), so a new file is private by default — only the graph above, through `__init__.py`, is allowed. Next to it: `modules-acyclic` (no cycles), `core-layer` (core imports no module or process), `modules-layer` (only routers reach their process), `telegram-extra` (aiogram/aiogram-dialog/fluentogram only inside the `telegram` process) and `usecases-without-sqlalchemy`. The bot's request-time code reading only through usecases is a test, `tests/telegram/test_no_repositories_at_request_time.py`, since repositories are exported from the same `__init__.py` as usecases and the import graph can't tell them apart. Run via `uv run lint-imports` (part of the standard verification command).
