import pytest

from harness.reliability.budget import BudgetTracker, BudgetExceeded
from harness.usage import Usage


def test_no_limits_never_raises():
    b = BudgetTracker()
    b.start()
    b.add_usage(Usage(0, 0, 10**9))
    b.check()  # 不抛


def test_token_budget_exceeded():
    b = BudgetTracker(max_tokens=100)
    b.start()
    b.add_usage(Usage(60, 60, 120))
    with pytest.raises(BudgetExceeded):
        b.check()


def test_time_budget_exceeded_with_fake_clock():
    ticks = iter([0.0, 5.0])  # start, check
    b = BudgetTracker(max_wall_seconds=3.0, clock=lambda: next(ticks))
    b.start()
    with pytest.raises(BudgetExceeded):
        b.check()


def test_total_tokens_accumulates():
    b = BudgetTracker()
    b.start()
    b.add_usage(Usage(1, 1, 2))
    b.add_usage(Usage(3, 3, 6))
    assert b.total_tokens == 8
