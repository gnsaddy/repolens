"""Slow down password guessing: a few wrong attempts for one account from one address pause further attempts.

In-process and per worker, which is enough to stop casual guessing; put a gateway rate limit in front for more.
"""

import threading
import time

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _recent(key: str, window: int) -> list[float]:
    cutoff = time.monotonic() - window
    recent = [t for t in _failures.get(key, []) if t > cutoff]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    return recent


def blocked_for(key: str, limit: int, window: int) -> int:
    """Seconds until another attempt is allowed (0 when not blocked)."""
    with _lock:
        recent = _recent(key, window)
        if len(recent) < limit:
            return 0
        return max(1, int(recent[0] + window - time.monotonic()))


def fail(key: str, window: int) -> None:
    with _lock:
        _recent(key, window)
        _failures.setdefault(key, []).append(time.monotonic())


def clear(key: str) -> None:
    with _lock:
        _failures.pop(key, None)


def reset() -> None:
    with _lock:
        _failures.clear()
