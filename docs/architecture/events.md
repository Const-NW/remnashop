# Events

## Rules

- **`core/events.py` holds shared base classes**: `BaseEvent` (bare fact, mandatory `category: ClassVar[EventCategory]`), `SystemEvent`, `UserEvent` (carries `user: UserDto`), `ActorEvent` (carries `actor: UserDto`), `ActorTargetEvent` (an `ActorEvent` that also carries `target: UserDto`, for admin-on-user actions). `EventCategory` is an `UpperStrEnum` (`AUTH`, `ADMIN_MANAGEMENT`, `USER`, `SETTINGS`, `SUBSCRIPTION`, `PROMOCODE`, `REFERRAL`, `PRICING`, `PLANS`, `PAYMENT`, `BROADCAST`, `INFRASTRUCTURE`) every concrete event must set. Concrete events live with the module that publishes them (`modules/auth/events.py`, `modules/users/events.py`), never centrally.
- **Event class**: `@dataclass(frozen=True, kw_only=True)` subclassing `BaseEvent`/`UserEvent`/`SystemEvent`/`ActorEvent`/`ActorTargetEvent`, always setting `category: ClassVar[EventCategory] = EventCategory.<...>` — the catch-all audit subscriber (see below) reads `event.category` on every event, so a class that omits it breaks that subscriber the moment it's published.
- **Subscribers live in the module's own `subscribers.py`** (event classes stay in `events.py`), which exports `register_subscribers(bus: EventBus) -> None`. `app/worker/subscribers.py`'s `register_all_subscribers()` imports each of them explicitly and calls it — a new module with subscribers adds one import and one call there; `core` never imports a module. Today: `advertising`, `auth`, `customer_notifications`, `loyalty`, `notifications`, `oauth`, `referrals`, `transactions`. A handler takes `(event, entry: StoredEvent)`: `bus.subscribe(EventClass, handler)` restores the typed event from the stored row; `bus.subscribe_stored("<EventType>", handler)` takes the raw `StoredEvent` for a subscriber that only knows event types as strings (`notifications`). Events with no type-specific subscriber are still persisted to the audit log (every published event is, via the outbox below).
- **Publish via the injected `EventPublisher` port** (`core/ports.py`) from inside a usecase (see [usecases.md](usecases.md)), instead of calling `logger.*` directly for anything a subscriber might care about.
- **Publishing goes through a transactional outbox, not straight to `EventBus`.** The `EventPublisher` port (`Scope.REQUEST`) is bound to `core/outbox_event_publisher.py`'s `OutboxEventPublisher`, not to `EventBus` — `EventBus` has no `publish()` of its own. `OutboxEventPublisher.publish()` writes an `EventOutboxEntry` row in the *same* DB transaction as the calling usecase (`flush()`, no commit — the usecase's own `UnitOfWork` commits it) and registers the worker kick through `UnitOfWork.after_commit()` (the `OutboxEntryDispatch` port, wrapped in `contextlib.suppress(Exception)` since the row is already durable either way) — nothing is sent for a transaction that rolls back.
- **Two consumers process the outbox, both in the `worker` process** (`modules/event_log/tasks.py`): `process_outbox_entry_task` runs off the after-commit kick; `sweep_outbox_task` runs on a `*/1 * * * *` cron and re-processes anything still unprocessed, so nothing depends on the kick arriving. Both run `ProcessOutboxEntryUseCase`: load the row, write it permanently to the audit log (`EventLogRepository.create`) — unconditional, every published event is audited; a `UserDeletedEvent` also anonymizes that customer's log and outbox entries in the same transaction — then call `bus.dispatch_stored(stored)` for any type-specific handler. A handler failure leaves the row `unprocessed` for the next sweep to retry (the audit write already committed); `prune_outbox_task` deletes processed rows older than 14 days on a daily cron, `prune_event_log_task` drops the noisy types listed in `event_log/retention.py` after 90 days.
- **`EventBus` itself is just the type-specific dispatch table**: `subscribe(EventClass, handler)`, `subscribe_stored(event_type, handler)`, `dispatch_stored(entry)` (runs every handler for that event type, isolating failures — see below), `shutdown()`. It holds no outbox/DB logic and never sees an event that hasn't already been persisted.
- **A subscriber's exception is caught and logged inside `dispatch_stored()`, never propagated** — one handler failing (a notification handler hitting a network error) must never stop the audit-log write or break another subscriber for the same event.
- **Cross-process, durable delivery.** The write happens wherever the usecase runs (`api`, `telegram`, ...); persistence and dispatch happen in `worker`, picked up via the outbox row, not an in-memory call — a crash between the usecase's commit and dispatch can't lose the event, since the row is already there for `sweep_outbox_task` to pick up. Only `app/worker/lifespan.py` calls `register_all_subscribers()` and `EventBus.shutdown()` — `dispatch_stored()` only ever runs from that process's outbox sweep, so `api`/`telegram` registering subscribers of their own would sit unused.

## Example

```python
@dataclass(frozen=True, kw_only=True)
class LoginFailedEvent(BaseEvent):
    category: ClassVar[EventCategory] = EventCategory.AUTH
    login: str

# modules/auth/subscribers.py
async def _log_login_failed(event: LoginFailedEvent, entry: StoredEvent) -> None:
    logger.warning(f"Failed login attempt for login='{event.login}'")

def register_subscribers(bus: EventBus) -> None:
    bus.subscribe(LoginFailedEvent, _log_login_failed)
```

Publishing from a usecase:

```python
await self._event_publisher.publish(BootstrapConflictEvent(login=login))
```
