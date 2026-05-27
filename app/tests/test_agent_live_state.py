"""AgentLiveState + wrap_notifier behaviour."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.live_state import AgentLiveState, wrap_notifier


class FakeNotifier:
    def __init__(self):
        self.seen: list[tuple[str, object]] = []

    async def added(self, dl):
        self.seen.append(('added', dl))

    async def updated(self, dl):
        self.seen.append(('updated', dl))

    async def completed(self, dl):
        self.seen.append(('completed', dl))

    async def canceled(self, id):
        self.seen.append(('canceled', id))

    async def cleared(self, id):
        self.seen.append(('cleared', id))


async def test_wrap_notifier_records_and_forwards():
    state = AgentLiveState()
    notifier = FakeNotifier()
    wrap_notifier(notifier, state)

    dl = SimpleNamespace(id='abc', title='Hello', status='downloading', percent=10, eta=5, url='u')
    await notifier.added(dl)
    await notifier.updated(dl)
    await notifier.completed(dl)
    await notifier.canceled('abc')

    # Forwarded
    kinds = [k for k, _ in notifier.seen]
    assert kinds == ['added', 'updated', 'completed', 'canceled']

    snap = state.snapshot()
    assert 'abc' in snap['latest_per_id']
    assert snap['latest_per_id']['abc']['kind'] == 'canceled'
    assert len(snap['recent_events']) == 4


async def test_live_state_rolling_log_cap():
    state = AgentLiveState()
    for i in range(60):
        state.record('updated', {'id': str(i)})
    snap = state.snapshot()
    assert len(snap['recent_events']) == AgentLiveState.MAX_LOG
