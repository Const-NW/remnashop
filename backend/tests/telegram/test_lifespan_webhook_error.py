from datetime import timedelta
from typing import Optional

import pytest
from aiogram.types import WebhookInfo

from app.core.utils.time import datetime_now
from app.telegram.lifespan import is_live_webhook_error


@pytest.mark.parametrize(
    ("message", "seconds_after_start", "pending", "expected"),
    [
        ("Wrong response", 30, 5, True),
        ("Connection refused", -10, 5, False),
        ("Wrong response", 30, 0, False),
        ("Wrong response", None, 5, False),
        (None, 30, 5, False),
    ],
    ids=["failing", "downtime", "recovered", "no-date", "no-error"],
)
def test_only_an_error_after_startup_with_updates_queued_counts(
    message: Optional[str], seconds_after_start: Optional[int], pending: int, expected: bool
) -> None:
    started = datetime_now().replace(microsecond=0)
    date = None if seconds_after_start is None else started + timedelta(seconds=seconds_after_start)
    info = WebhookInfo(
        url="https://x",
        has_custom_certificate=False,
        pending_update_count=pending,
        last_error_message=message,
        last_error_date=date,
    )

    assert is_live_webhook_error(info, started) is expected
