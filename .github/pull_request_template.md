## What and why

<!-- What the PR changes and the problem it solves. Link the issue or discussion. -->

## What changes for shops that don't use this

<!-- Shared code touched, behavior differences, new queries on hot paths. "Nothing" is fine. -->

## Checklist

Rules: [AGENTS.md](https://github.com/remnashop/remnashop/blob/dev/AGENTS.md). Process: [CONTRIBUTING.md](https://github.com/remnashop/remnashop/blob/dev/.github/CONTRIBUTING.md).

- [ ] One concern; under ~800 lines of application code, or the split was agreed
- [ ] The description matches the final code
- [ ] Logic is in usecases; repositories only read and write; everything built by DI
- [ ] No payment-provider or third-party call inside a transaction or under a row lock
- [ ] Events go through `EventPublisher`; each new event type is pruned or kept on purpose (`event_log/retention.py`)
- [ ] Money: idempotency key stored first, payment confirmed through the provider API, lifecycle table in the description
- [ ] Consent to future charges is explicit and off by default; customer-facing text is editable
- [ ] API models are `*Request`/`*Response` in `api/schemas.py`; methods, statuses and error codes follow `AGENTS.md`
- [ ] Migration reviewed by hand, `downgrade` works, foreign key `ondelete` considered
- [ ] Bot text through `render_window_text`, buttons through the dialog catalog or `label_with_icon`, dates through `format_datetime`
- [ ] Dashboard built from `shared/ui` primitives; strings in both locales
- [ ] Bot and cabinet both handled, or the cabinet stays unchanged
- [ ] Verification commands from `AGENTS.md` pass locally
