# Payment gateways

Every HTTP gateway in `modules/transactions/gateways/` follows one contract
(`base.py`):

- `create_payment(order, text)` creates the provider payment. `text`
  (`PaymentTextDto`) carries the order's description from translation keys,
  built through the `PaymentTexts` port. The link lifetime is derived from
  `order.expires_at` wherever the provider accepts one.
- `parse_webhook(request)` authenticates the notification where the provider
  signs it and returns a `WebhookSignal`. It never decides that an order is paid.
- `fetch_payment(order, signal)` reads the payment from the provider's status
  API and returns a `ProviderPayment`.
- `confirm(order, signal)` (base class only) credits the order when the
  provider reports it paid, `ProviderPayment.reference` equals the order's
  `idempotency_key`, the amount (when reported) is at least `order.final_price`,
  and the currency (when reported) matches. `reference` is a required field —
  the check is fail-closed, not skipped when a provider omits it. Adapters
  whose status response doesn't echo our own reference back (Platega,
  Robokassa) report the order's own `idempotency_key` instead, since their
  status query is already bound to that specific order.
- `confirm_reported(order, signal)` handles a non-paid status (canceled,
  failed, refunded): returns the status as the provider's API confirms it, or
  `None` to ignore the notification. The base trusts a signed notification.
- `webhook_ack(signal)` is the response body/status the provider expects back.

YooMoney is the one exception: without an `access_token` setting it trusts the
HMAC-signed notification and its `withdraw_amount`.

Shared signing, parsing and lifetime helpers live in `gateways/common.py`;
`registry.py`/`factory.py` map a `PaymentGatewayType` to its adapter.
Telegram Stars (`telegram_stars.py`) has no webhook: the bot's
`successful_payment` handler settles the order, `confirm_stars_payment_task`
is the worker fallback.

## Webhook source addresses

Provider API confirmation is the security boundary. The one in-app source
filter is YooKassa's: its adapter declares `trusted_networks`, checked by
`PaymentGateway.is_trusted_source` while the `verify_ip` setting is on (the
default). For other gateways, operators who want an allowlist can add one on
the reverse proxy, for example:

```nginx
location ~* ^/api/v1/payments/yookassa$ {
    allow 185.71.76.0/27;
    allow 185.71.77.0/27;
    deny  all;
    proxy_pass http://127.0.0.1:5000;
}
```

The reverse proxy must pass the real client address (`$remote_addr`); behind
Cloudflare, restore it with `real_ip_header CF-Connecting-IP` first.

| Gateway | Documented addresses |
|---|---|
| YooKassa | 185.71.76.0/27, 185.71.77.0/27, 77.75.153.0/25, 77.75.156.11, 77.75.156.35, 77.75.154.128/25, 2a02:5180::/32 |
| Robokassa | 185.59.216.65, 185.59.217.65 |
| FreeKassa | 168.119.157.136, 168.119.60.227, 178.154.197.79, 51.250.54.238 |
| Cryptomus | 91.227.144.54 |
| Heleket | 31.133.220.8 |
| SeverPay | 45.76.81.14, 207.148.69.64, 2001:19f0:6c01:878:5400:5ff:fe38:50d1, 2401:c080:1400:109b:5400:5ff:fe95:20d3 |
| PayPear | 158.160.85.101 |
| Antilopay | 81.177.221.226, 87.228.9.243 |
| CloudPayments | 185.98.81.0/28, 87.251.91.160/27, 46.46.175.96/27, 46.46.168.160/27, 162.55.174.97, 91.216.178.243 |

Other gateways publish no addresses.
