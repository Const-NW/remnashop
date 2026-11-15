# Observability

## Rules

- **Off unless `APP_METRICS_ENABLED=true`.** With it off, `api` has no `/metrics` route and `worker` starts no HTTP server.
- **Two processes, two Prometheus exposition points, both unauthenticated.** `api` exposes `GET /metrics` on its normal port (`prometheus-fastapi-instrumentator`, wired in `app/api/app.py` with `metric_namespace="remnashop"`); `worker` runs its own dedicated `prometheus_client` HTTP server on `APP_METRICS_PORT` (default `9110`, `app/worker/lifespan.py`). Neither requires a token — same trust model as `/health`/`/readyz`. **Block both at the reverse proxy in production**; nothing in the application itself restricts access.
- **With `WORKER_PROCESSES` > 1 only one worker process serves metrics.** `docker-entrypoint.sh` runs `taskiq worker -w ${WORKER_PROCESSES:-1}`; on `WORKER_STARTUP` each process tries to bind `APP_METRICS_PORT` (`app/worker/lifespan.py`'s `_start_metrics_server`), the first one wins and alone runs the worker-gauges poller, the rest log it and go without. So queue/outbox gauges are sampled once, but the per-process counters and histograms (task durations, email and notification counters, DB pool) cover only the process that owns the port. Full per-process numbers would need a shared `PROMETHEUS_MULTIPROC_DIR` setup.
- **`api`'s custom gauges must pass `registry=` explicitly, matching whichever registry the process actually serves.** `api` serves the default global `prometheus_client.REGISTRY` (what `Instrumentator` uses); `worker` serves its own `WORKER_METRICS_REGISTRY` (`app/worker/metrics.py`). A gauge constructed without an explicit `registry=` argument silently lands in the global one, so a worker-only metric shows up on `api`'s `/metrics` stuck at zero. See `app/core/metrics.py`'s `register_db_pool_metrics()` for the pattern: `registry` is a required keyword argument with no default, precisely so a call site can't forget which one it means.
- **Two update patterns, pick by cost.** A pure in-memory read (SQLAlchemy pool stats) uses `Gauge.set_function()` — a zero-arg callback invoked fresh on every scrape, no polling loop needed. Anything requiring I/O (a Redis `LLEN`/`SCAN`) goes through a background `asyncio` poller instead (`app/worker/metrics.py`'s `poll_worker_gauges`, on a 15s interval) — scraping shouldn't block on a network round trip.
- **A renamed or removed metric is a breaking change** for every dashboard and alert rule built on it. The `_total` suffix is for counters only, never a gauge.

## Current metrics

| Metric | Type | Process / registry | What it means |
|---|---|---|---|
| `remnashop_http_request_duration_seconds`, `remnashop_http_requests_total` (+ instrumentator defaults) | histogram/counter | `api` / default `REGISTRY` | Per-route HTTP latency and count, labeled by handler/method/status class |
| `remnashop_db_pool_size` | gauge | both / each process's own registry | Configured SQLAlchemy pool size (`register_db_pool_metrics`, `app/core/metrics.py`) |
| `remnashop_db_pool_checked_out` | gauge | both | Connections currently checked out of the pool |
| `remnashop_rate_limit_hits_total` | counter | `api` / default `REGISTRY` | Requests rejected by a rate limiter, labeled by the bounded `key` set (`app/api/dependencies/rate_limit.py`) |
| `remnashop_event_outbox_unprocessed` | gauge | `worker` / `WORKER_METRICS_REGISTRY` | Unprocessed `event_outbox` rows, refreshed on every `sweep_outbox_task` tick (`app/modules/event_log/tasks.py`) |
| `remnashop_event_outbox_oldest_unprocessed_age_seconds` | gauge | `worker` | Age of the oldest unprocessed outbox row |
| `remnashop_taskiq_queue_depth` | gauge | `worker` | Pending messages in the main taskiq Redis list queue (`app/worker/metrics.py`) |
| `remnashop_taskiq_scheduled_retries` | gauge | `worker` | Pending delayed `SmartRetryMiddleware` retries — a separate Redis key space `queue_depth` doesn't see |
| `remnashop_taskiq_task_duration_seconds` | histogram | `worker` | Per-task execution time, labeled `(task_name, outcome)`, fed by `TaskMetricsMiddleware.post_execute` — fires after every task run regardless of outcome |
| `remnashop_broadcast_emails_total` | counter | `worker` | Broadcast emails sent, labeled `result` (`modules/broadcasts/metrics.py`) |
| `remnashop_customer_notifications_total` | counter | `worker` | Customer notifications, labeled `(channel, result)` (`modules/customer_notifications/metrics.py`) |

No Prometheus server, dashboards, or alerting rules are part of this repo — the above is what a self-hosted Prometheus would scrape from `api`/`worker`, nothing more.
