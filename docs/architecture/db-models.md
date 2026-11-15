# DB Models

## Rules

- **All SQLAlchemy models are centralized in `core/db/models/`**, one file per table, regardless of which module owns the domain. `BaseSql.metadata` is a single global registry — module-local models only gave the illusion of isolation while still needing central migration bookkeeping. Adding a model: one new file + one import line in `core/db/models/__init__.py`'s `__all__`.
- **Column by default.** JSON (`postgresql.JSONB`) is reserved for:
  - a verbatim passthrough of a format we don't own (an external API/protocol's own config), or
  - a settings bundle always read and written as one unit, never filtered/sorted/joined on individually.

  Anything you'd ever `WHERE`/`ORDER BY`/`JOIN` on, or that needs a DB-level `NOT NULL`/type/constraint, stays a real column even if it's "structured data" — an address or a port number doesn't become JSON just because it's part of a config object. In practice every JSON-shaped field uses raw `postgresql.JSONB` directly: open string sets (`User.permission_grants`/`permission_revokes`/`ip_allowlist`, `ApiToken.scopes`/`ip_allowlist`, `Promocode.allowed_user_ids`), event payloads (`EventLogEntry.payload`, `EventOutboxEntry.payload`), immutable snapshots (`Subscription.plan_snapshot`, `Transaction.plan_snapshot`, `DialogWindow.default_snapshot`) and small always-atomic bundles (`ReferralSettings.reward_amounts`, `AppearanceSettings.shader_palette`). There is no `PydanticJSON` wrapper type: (de)serialize in the repository's `_to_dto`/write methods when a field needs a Pydantic model on top of plain JSON.
- **`class Foo(BaseSql, TimestampMixin): __tablename__ = "..."`** — `TimestampMixin` gives `created_at`/`updated_at` for free, don't redeclare them.
- **DB-level constraints for actual invariants, not just app-level checks.** An app-level `if` is TOCTOU-racy under concurrent writes; a real constraint isn't. Use judgment on stakes: `User.ux_users_single_owner` (partial unique index on `role`) enforces the one-`OWNER` invariant at the DB level because a race there is a real security property. A low-stakes singleton table (`BotSettings`) gets away with a fixed `id=1` and get-or-create in the repository — no DB constraint needed.
- **Secrets at rest use `EncryptedString`** (`core/db/encrypted_string.py`), a `TypeDecorator` wrapping `String` — Fernet-encrypts on bind, decrypts on result, transparently. The mapped attribute is always plaintext at the Python level, same as any other `str` column; ciphertext exists only in the DB and briefly inside the type decorator's own bind/result methods. Repositories never call encrypt/decrypt by hand — see [repositories.md](repositories.md).

## Example

```python
class User(BaseSql, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "ux_users_single_owner", "role", unique=True,
            postgresql_where=text(f"role = '{Role.OWNER.name}'"),
        ),
        CheckConstraint(
            "login IS NULL OR login ~ '^[A-Za-z0-9._-]+$'",
            name="login_charset",  # naming convention makes it ck_users_login_charset
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[Optional[str]] = mapped_column(String(64), index=True, unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(161))
    role: Mapped[Role] = mapped_column(index=True)
    is_blocked: Mapped[bool]

    # open string sets, never queried element by element
    permission_grants: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    permission_revokes: Mapped[Optional[list[str]]] = mapped_column(JSONB)
    ip_allowlist: Mapped[Optional[list[str]]] = mapped_column(JSONB)
```

```python
class BotSettings(BaseSql, TimestampMixin):
    __tablename__ = "bot_settings"

    # single row, always id=1 (modules/settings/repository.py)
    id: Mapped[int] = mapped_column(primary_key=True)

    # Fernet-encrypted with APP_CRYPT_KEY by EncryptedString
    token: Mapped[Optional[str]] = mapped_column(EncryptedString())
```
