"""Minimal synchronous publish/subscribe event bus used to decouple core components."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

Handler = Callable[[dict], None]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: Handler) -> None:
        self._subscribers[event_name].append(handler)

    def unsubscribe(self, event_name: str, handler: Handler) -> None:
        if handler in self._subscribers.get(event_name, []):
            self._subscribers[event_name].remove(handler)

    def publish(self, event_name: str, data: dict[str, Any] | None = None) -> None:
        for handler in list(self._subscribers.get(event_name, [])):
            handler(data or {})
