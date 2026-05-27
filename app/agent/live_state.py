"""In-process snapshot of download events for the agent's `get_live_state` tool.

Wraps an existing `Notifier` (the one MeTube uses to fan events to
Socket.IO) so we observe the same events without a network roundtrip.
"""

from __future__ import annotations

import time
from typing import Any


class AgentLiveState:
    """Holds the most recent download event per id + a short rolling log."""

    MAX_LOG = 50

    def __init__(self) -> None:
        self._latest: dict[str, dict] = {}
        self._log: list[dict] = []

    def snapshot(self) -> dict:
        return {
            'latest_per_id': dict(self._latest),
            'recent_events': list(self._log),
        }

    def record(self, kind: str, payload: Any) -> None:
        item = {'kind': kind, 'ts': time.time(), 'payload': self._summarise(payload)}
        # Track per-id when possible
        ident = None
        if isinstance(payload, dict):
            ident = payload.get('id') or payload.get('url')
        elif hasattr(payload, 'id'):
            ident = getattr(payload, 'id', None)
        elif isinstance(payload, str):
            ident = payload
        if ident:
            self._latest[str(ident)] = item
        self._log.append(item)
        if len(self._log) > self.MAX_LOG:
            self._log = self._log[-self.MAX_LOG:]

    @staticmethod
    def _summarise(obj: Any) -> Any:
        if hasattr(obj, '__dict__'):
            d = obj.__dict__
            return {k: d[k] for k in ('id', 'url', 'title', 'status', 'percent', 'eta')
                    if k in d}
        return obj


def wrap_notifier(notifier: Any, state: AgentLiveState) -> None:
    """Monkey-patch a `Notifier` instance so its async events also feed `state`.

    Cleaner than subclassing because MeTube constructs the Notifier
    inline at module level and we want to attach after the fact.
    """
    for kind in ('added', 'updated', 'completed', 'canceled', 'cleared'):
        orig = getattr(notifier, kind, None)
        if orig is None:
            continue

        def make_wrapper(_orig, _kind):
            async def wrapper(arg):
                state.record(_kind, arg)
                return await _orig(arg)
            return wrapper

        setattr(notifier, kind, make_wrapper(orig, kind))
