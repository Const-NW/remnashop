from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from app.core.enums import CreditSegmentKind, Currency
from app.modules.subscriptions.credit import remaining_term_credit, traffic_pace, unlimited_credit
from app.modules.subscriptions.dto import CreditSegmentDto

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DAY = timedelta(days=1)


def _segment(
    paid: str,
    *,
    starts: int,
    days: int,
    currency: Currency = Currency.RUB,
    superseded: bool = False,
    reset: Optional[int] = None,
    pace: str = "1",
    payment_key: Optional[str] = None,
) -> CreditSegmentDto:
    """A term from day `starts` (0 = now) lasting `days`."""
    return CreditSegmentDto(
        id=1,
        subscription_id=1,
        kind=CreditSegmentKind.PURCHASE,
        paid=Decimal(paid),
        currency=currency,
        seconds=days * 86400,
        ends_at=_NOW + (starts + days) * _DAY,
        superseded_by_id=2 if superseded else None,
        payment_key=payment_key,
        traffic_reset_at=_NOW + reset * _DAY if reset is not None else None,
        traffic_pace=Decimal(pace),
        created_at=_NOW,
        updated_at=_NOW,
    )


def _same(amount: Decimal, currency: Currency) -> Decimal:
    return amount


def _credit(*segments: CreditSegmentDto, expire_in: int) -> Decimal:
    return remaining_term_credit(
        segments,
        now=_NOW,
        expire_at=_NOW + expire_in * _DAY,
        convert=_same,
    )


def test_each_day_left_is_worth_what_was_paid_for_it() -> None:
    # a month at 100 then a discounted year at 360; 40 days in, 355 days of the year left
    month = _segment("100", starts=-40, days=30)
    year = _segment("360", starts=-10, days=365)

    assert _credit(month, year, expire_in=355) == Decimal("360") * 355 / 365


def test_a_dear_month_after_a_cheap_year_is_credited_at_its_own_price() -> None:
    year = _segment("360", starts=-370, days=365)
    month = _segment("100", starts=-5, days=30)

    assert _credit(year, month, expire_in=25) == Decimal("100") * 25 / 30


def test_days_past_the_last_segment_are_free() -> None:
    # an admin added 10 days on top of a paid month
    assert _credit(_segment("300", starts=0, days=30), expire_in=40) == Decimal("300")


def test_a_term_cut_short_loses_its_tail() -> None:
    assert _credit(_segment("300", starts=0, days=30), expire_in=10) == Decimal("100")


def test_a_lapsed_segment_credits_nothing() -> None:
    assert _credit(_segment("300", starts=-60, days=30), expire_in=0) == Decimal("0")


def test_a_superseded_segment_credits_nothing() -> None:
    assert _credit(_segment("300", starts=0, days=30, superseded=True), expire_in=30) == 0


def test_each_segment_converts_from_its_own_currency() -> None:
    def to_rub(amount: Decimal, currency: Currency) -> Decimal:
        return amount * 90 if currency is Currency.USD else amount

    segments = [
        _segment("3", starts=0, days=30, currency=Currency.USD),
        _segment("300", starts=30, days=30),
    ]

    credit = remaining_term_credit(segments, now=_NOW, expire_at=_NOW + 60 * _DAY, convert=to_rub)

    assert credit == Decimal("570")


def _unlimited(paid: str, *, superseded: bool = False) -> CreditSegmentDto:
    return _segment(paid, starts=0, days=0).model_copy(
        update={"ends_at": None, "superseded_by_id": 2 if superseded else None}
    )


def test_unlimited_credit_is_what_is_paid_and_not_replaced_or_used_up() -> None:
    segments = [
        _segment("300", starts=0, days=30, superseded=True),
        _unlimited("1000"),
        _unlimited("500"),
        # an admin made it unlimited: a fixed term half used, another long past
        _segment("200", starts=-15, days=30),
        _segment("900", starts=-400, days=365),
    ]

    assert unlimited_credit(segments, now=_NOW, convert=_same) == Decimal("1600")


def test_a_renewal_starting_inside_a_cut_term_covers_the_shared_days_once() -> None:
    # bought a month, cut short to day 19, renewed from there; the cut term's days
    # past the renewal's start are the renewal's
    cut = _segment("300", starts=-14, days=30)
    renewal = _segment("300", starts=5, days=30)

    assert _credit(cut, renewal, expire_in=35) == Decimal("300") + Decimal("300") * 5 / 30


def _pace(*segments: CreditSegmentDto, expire_in: int, traffic_left: str) -> Decimal:
    return traffic_pace(
        segments, now=_NOW, expire_at=_NOW + expire_in * _DAY, traffic_left=Decimal(traffic_left)
    )


def test_traffic_used_no_faster_than_time_cuts_nothing() -> None:
    month = _segment("300", starts=-10, days=30, reset=-10)

    assert _pace(month, expire_in=20, traffic_left="0.7") == 1


def test_traffic_used_ahead_of_time_cuts_the_credit_in_proportion() -> None:
    # 95% gone with two thirds of the time left
    month = _segment("300", starts=-10, days=30, reset=-10)

    assert _pace(month, expire_in=20, traffic_left="0.05") == Decimal("0.05") * 3 / 2


def test_one_counter_covers_the_terms_paid_ahead() -> None:
    # renewed ahead on day 5: 80% left of the traffic for the 55 days from then
    month = _segment("300", starts=-15, days=30, reset=-15)
    ahead = _segment("300", starts=15, days=30, reset=-10)

    pace = _pace(month, ahead, expire_in=45, traffic_left="0.8")

    assert pace == Decimal("0.8") / (Decimal(45) / Decimal(55))


def test_a_cheap_renewal_after_using_it_all_up_unlocks_nothing() -> None:
    # the year's traffic was gone when a week bought only to zero the counter reset it:
    # that pace carries over the reset, fresh traffic or not
    year = _segment("3600", starts=-62, days=365, reset=-62)
    week = _segment("70", starts=303, days=7, reset=0, pace="0")

    assert _pace(year, week, expire_in=310, traffic_left="1") == 0


def test_the_pace_before_a_reset_carries_over_it() -> None:
    month = _segment("300", starts=-10, days=30, reset=-10)
    ahead = _segment("300", starts=20, days=30, reset=-5, pace="0.5")

    assert _pace(month, ahead, expire_in=50, traffic_left="1") == Decimal("0.5")


def test_an_order_that_left_the_counter_alone_does_not_move_the_reset() -> None:
    # a referral bonus day redeemed today: the counter still runs from the month's purchase
    month = _segment("300", starts=-10, days=30, reset=-10)
    bonus = _segment("0", starts=20, days=1, payment_key="bonus")

    assert _pace(month, bonus, expire_in=21, traffic_left="0.05") == Decimal("0.05") / (
        Decimal(21) / Decimal(31)
    )


def test_without_a_reset_on_record_the_running_term_starts_the_counter() -> None:
    legacy = _segment("300", starts=-10, days=30)

    assert _pace(legacy, expire_in=20, traffic_left="0.05") == Decimal("0.05") * 3 / 2
