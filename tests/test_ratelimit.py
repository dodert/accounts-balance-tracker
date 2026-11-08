import pytest

from app.ratelimit import TooManyTries, TryLimit


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def wrong(limit, key):
    with limit.attempt(key) as attempt:
        attempt.wrong()


def test_waits_until_the_oldest_wrong_try_leaves_the_window():
    clock = Clock()
    limit = TryLimit(3, 60, clock=clock)
    for _ in range(3):
        wrong(limit, "a")
        clock.now += 10
    with pytest.raises(TooManyTries) as error:
        wrong(limit, "a")
    assert error.value.retry_after == 30  # first try at 1000, now 1030: free again at 1060
    clock.now += 30
    wrong(limit, "a")  # allowed again, and counts
    with pytest.raises(TooManyTries):
        wrong(limit, "a")


def test_other_keys_are_not_affected():
    limit = TryLimit(1, 60, clock=Clock())
    wrong(limit, "a")
    with pytest.raises(TooManyTries):
        wrong(limit, "a")
    wrong(limit, "b")


def test_one_check_at_a_time_per_key():
    limit = TryLimit(5, 60, clock=Clock())
    with limit.attempt("a"):
        with pytest.raises(TooManyTries):
            with limit.attempt("a"):
                pass
        with limit.attempt("b"):
            pass
    with limit.attempt("a"):  # free again once the first check ended
        pass


def test_total_limit_across_keys():
    clock = Clock()
    limit = TryLimit(5, 60, allowed_total=3, clock=clock)
    for key in "abc":
        wrong(limit, key)
    with pytest.raises(TooManyTries):
        wrong(limit, "d")
    clock.now += 60
    wrong(limit, "d")


def test_reset_forgets_a_key():
    limit = TryLimit(1, 60, clock=Clock())
    wrong(limit, "a")
    limit.reset("a")
    wrong(limit, "a")


def test_memory_is_bounded():
    limit = TryLimit(2, 60, max_keys=100, clock=Clock())
    for i in range(1000):
        wrong(limit, f"ip{i}")
    assert len(limit._wrong) == 100
    assert all(len(tries) <= 2 for tries in limit._wrong.values())
