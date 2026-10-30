from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


_WINDOW_SECONDS = 15 * 60
_LOCKOUT_SECONDS = 15 * 60
_MAX_FAILURES = 5
_lock = threading.Lock()
_failures = defaultdict(deque)
_locked_until = {}


def _key(username: str, ip_address: str) -> str:
    return f"{(username or '').strip().upper()}|{(ip_address or '').strip()}"


def is_blocked(username: str, ip_address: str) -> bool:
    now = time.monotonic()
    key = _key(username, ip_address)
    with _lock:
        until = _locked_until.get(key, 0)
        if until > now:
            return True
        if until:
            _locked_until.pop(key, None)
        return False


def register_failure(username: str, ip_address: str) -> None:
    now = time.monotonic()
    key = _key(username, ip_address)
    cutoff = now - _WINDOW_SECONDS
    with _lock:
        bucket = _failures[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        bucket.append(now)
        if len(bucket) >= _MAX_FAILURES:
            _locked_until[key] = now + _LOCKOUT_SECONDS
            bucket.clear()


def clear_failures(username: str, ip_address: str) -> None:
    key = _key(username, ip_address)
    with _lock:
        _failures.pop(key, None)
        _locked_until.pop(key, None)
