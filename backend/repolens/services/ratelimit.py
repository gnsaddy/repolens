"""Rate limiting: slow down password guessing and keep one caller from using all the capacity.

In-process and per worker, which stops casual abuse; put a gateway limit in front for several replicas.
"""

import threading
import time

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
_hits: dict[str, list[float]] = {}
_last_sweep = 0.0


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


def hit(key: str, limit: int, window: int = 60) -> int:
    """Count a request; returns 0 when allowed, else the seconds to wait. Sliding window."""
    global _last_sweep
    now = time.monotonic()
    with _lock:
        if now - _last_sweep > 120:  # forget idle callers so memory stays bounded
            _last_sweep = now
            for k in [k for k, v in _hits.items() if not v or v[-1] < now - 600]:
                _hits.pop(k, None)
        q = [t for t in _hits.get(key, []) if t > now - window]
        if len(q) >= limit:
            _hits[key] = q
            return max(1, int(q[0] + window - now))
        q.append(now)
        _hits[key] = q
        return 0


def reset() -> None:
    global _last_sweep
    with _lock:
        _failures.clear()
        _hits.clear()
        _last_sweep = 0.0
