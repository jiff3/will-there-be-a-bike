"""Bounded per-worker metrics; no request labels containing secrets or user IDs."""

from collections import Counter

requests: Counter[str] = Counter()
events: Counter[str] = Counter()
durations_ms: dict[str, float] = {}


def record(route: str, status: int, elapsed: float) -> None:
    key = f"{route}:{status}"
    requests[key] += 1
    durations_ms[route] = durations_ms.get(route, 0) + elapsed
