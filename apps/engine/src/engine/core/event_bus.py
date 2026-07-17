"""
Event Bus - generic async in-process pub/sub.

Per the architecture diagram this box is reused for two distinct
purposes within one session:

  1. "Event Bus" - raw SimEvents fan out to whichever Pluggable
     Continuous Identifiers declared interest in that event type.
  2. "Evidence Events" - Identifiers publish `Evidence` onto a second
     instance of this same class; the Evidence Normalizer -> Belief
     Engine pipeline is the (only) subscriber.

Deliberately dumb: no persistence, no cross-process delivery, no
ordering guarantees beyond "subscribers are awaited in registration
order". A single interview session is low enough volume that this is
not a bottleneck; if Sherlock later needs multi-process fan-out (e.g.
identifiers running as separate workers), this is the seam to swap for
Redis pub/sub / NATS / a real queue without touching call sites - every
call site only knows about `.subscribe(topic, handler)` and
`await .publish(topic, payload)`.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

logger = logging.getLogger("engine.event_bus")

Handler = Callable[[Any], Awaitable[None]]

# Subscribing to WILDCARD means "receive everything published on this
# bus, regardless of topic" - used by e.g. a raw event logger, or the
# evidence bus's single normalizer subscriber.
WILDCARD = "*"


class EventBus:
    def __init__(self, name: str = "bus") -> None:
        self.name = name
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Register `handler` for `topic` (or WILDCARD for everything).
        Returns an unsubscribe callback."""
        self._subscribers[topic].append(handler)

        def unsubscribe() -> None:
            handlers = self._subscribers.get(topic)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, topic: str, payload: Any) -> None:
        """Deliver `payload` to every handler subscribed to `topic` and
        every WILDCARD handler. Handlers run sequentially and are
        isolated: one handler raising never stops the others or the
        caller (errors are logged, not propagated) - a single bad or
        experimental identifier should never be able to take down
        identification for the rest of the session."""
        handlers = list(self._subscribers.get(topic, ())) + (
            list(self._subscribers.get(WILDCARD, ())) if topic != WILDCARD else []
        )
        for handler in handlers:
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - intentionally broad, see docstring
                logger.exception(
                    "[%s] subscriber %r raised while handling topic %r",
                    self.name,
                    getattr(handler, "__qualname__", handler),
                    topic,
                )
