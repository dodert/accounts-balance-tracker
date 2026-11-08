"""Limits on wrong login tries: per client IP, so a guesser is slowed down without locking anyone else out."""
import math
import time
from collections import OrderedDict, deque
from contextlib import contextmanager


class TooManyTries(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"Too many tries, retry in {retry_after} s")
        self.retry_after = retry_after


class Attempt:
    def __init__(self, limit: "TryLimit", key: str):
        self._limit, self._key = limit, key

    def wrong(self) -> None:
        """Count this try as wrong."""
        self._limit._record(self._key)


class TryLimit:
    """At most `allowed` wrong tries per key (the client IP) in `window` seconds, and one check at a time
    per key, so tries sent in parallel from one IP can't get past the limit. With `allowed_total`, also
    at most that many wrong tries in the window from all IPs together.

    Only wrong tries are stored, at most `allowed` per key and `max_keys` keys (the oldest are forgotten)."""

    def __init__(self, allowed: int, window: float, allowed_total: int | None = None, max_keys: int = 10_000,
                 clock=time.monotonic):
        self.allowed, self.window, self.allowed_total = allowed, window, allowed_total
        self.max_keys, self.clock = max_keys, clock
        self._wrong: OrderedDict[str, deque[float]] = OrderedDict()
        self._wrong_total: deque[float] = deque(maxlen=allowed_total or 1)
        self._checking: set[str] = set()

    def _wait(self, tries: deque[float], allowed: int, now: float) -> int:
        """Seconds until one more try is allowed (0 = now)."""
        while tries and tries[0] <= now - self.window:
            tries.popleft()
        if len(tries) < allowed:
            return 0
        return max(1, math.ceil(tries[-allowed] + self.window - now))

    def retry_after(self, key: str) -> int:
        now = self.clock()
        wait = self._wait(self._wrong[key], self.allowed, now) if key in self._wrong else 0
        if self.allowed_total:
            wait = max(wait, self._wait(self._wrong_total, self.allowed_total, now))
        return wait

    @contextmanager
    def attempt(self, key: str):
        """Raises TooManyTries when this key has to wait, or already has a check running; otherwise
        yields an Attempt whose wrong() counts the try."""
        if key in self._checking:
            raise TooManyTries(1)
        wait = self.retry_after(key)
        if wait:
            raise TooManyTries(wait)
        self._checking.add(key)
        try:
            yield Attempt(self, key)
        finally:
            self._checking.discard(key)

    def _record(self, key: str) -> None:
        now = self.clock()
        tries = self._wrong.pop(key, None) or deque(maxlen=self.allowed)
        tries.append(now)
        self._wrong[key] = tries  # newest last
        while len(self._wrong) > self.max_keys:
            self._wrong.popitem(last=False)
        if self.allowed_total:
            self._wrong_total.append(now)

    def reset(self, key: str) -> None:
        """Forget the wrong tries of this key (after a right one)."""
        self._wrong.pop(key, None)
