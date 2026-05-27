"""HITL semantics for delete_downloads and delete_subscriptions.

We exercise the two tools' confirmation branches by constructing a
ToolContext-like object with `tool_confirmation` set or unset, and a
`request_confirmation` method that records the hint+payload.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import tools as agent_tools
from test_agent_tools import FakeConfig, FakeDQueue, FakeSubMgr


class FakeToolContext:
    def __init__(self, confirmation=None):
        self.tool_confirmation = confirmation
        self.request_calls: list[dict] = []

    def request_confirmation(self, *, hint=None, payload=None):
        self.request_calls.append({'hint': hint, 'payload': payload})


@pytest.fixture
def bound(tmp_path):
    dq = FakeDQueue()
    sm = FakeSubMgr()
    cfg = FakeConfig(str(tmp_path / 'cookies.txt'))
    agent_tools.bind(
        dqueue=dq, submgr=sm, config=cfg,
        cookies_path=str(tmp_path / 'cookies.txt'), live_state=None,
    )
    return SimpleNamespace(dqueue=dq, submgr=sm)


async def test_delete_downloads_first_call_requests_confirmation(bound):
    ctx = FakeToolContext(confirmation=None)
    out = await agent_tools.delete_downloads(['a', 'b'], 'done', tool_context=ctx)
    assert out == {'status': 'awaiting_confirmation'}
    assert ctx.request_calls[0]['payload'] == {'ids': ['a', 'b'], 'where': 'done'}
    assert 'Delete 2' in ctx.request_calls[0]['hint']
    # No actual deletion happened
    assert bound.dqueue.cancel_args == []
    assert bound.dqueue.clear_args == []


async def test_delete_downloads_resume_approved_done(bound):
    confirmation = SimpleNamespace(confirmed=True, payload={'ids': ['a', 'b'], 'where': 'done'})
    ctx = FakeToolContext(confirmation=confirmation)
    out = await agent_tools.delete_downloads(['a', 'b'], 'done', tool_context=ctx)
    assert out == {'status': 'ok', 'cleared': ['a', 'b']}
    assert bound.dqueue.clear_args == [['a', 'b']]


async def test_delete_downloads_resume_approved_queue(bound):
    confirmation = SimpleNamespace(confirmed=True, payload={'ids': ['x'], 'where': 'queue'})
    ctx = FakeToolContext(confirmation=confirmation)
    out = await agent_tools.delete_downloads(['x'], 'queue', tool_context=ctx)
    assert out == {'status': 'ok', 'cancelled': ['x']}


async def test_delete_downloads_rejects_bad_where(bound):
    ctx = FakeToolContext(confirmation=None)
    out = await agent_tools.delete_downloads(['a'], 'nowhere', tool_context=ctx)
    assert out['status'] == 'error'
    assert ctx.request_calls == []


async def test_delete_downloads_cancelled(bound):
    confirmation = SimpleNamespace(confirmed=False, payload={'ids': ['a'], 'where': 'done'})
    ctx = FakeToolContext(confirmation=confirmation)
    out = await agent_tools.delete_downloads(['a'], 'done', tool_context=ctx)
    assert out == {'status': 'cancelled'}
    assert bound.dqueue.clear_args == []


async def test_delete_subscriptions_first_call(bound):
    ctx = FakeToolContext(confirmation=None)
    out = await agent_tools.delete_subscriptions(['s1', 's2'], tool_context=ctx)
    assert out == {'status': 'awaiting_confirmation'}
    assert ctx.request_calls[0]['payload'] == {'ids': ['s1', 's2']}
    assert bound.submgr.delete_args == []


async def test_delete_subscriptions_resume(bound):
    confirmation = SimpleNamespace(confirmed=True, payload={'ids': ['s1']})
    ctx = FakeToolContext(confirmation=confirmation)
    out = await agent_tools.delete_subscriptions(['s1'], tool_context=ctx)
    assert out == {'status': 'ok', 'deleted': ['s1']}


async def test_delete_subscriptions_cancelled(bound):
    confirmation = SimpleNamespace(confirmed=False, payload={'ids': ['s1']})
    ctx = FakeToolContext(confirmation=confirmation)
    out = await agent_tools.delete_subscriptions(['s1'], tool_context=ctx)
    assert out == {'status': 'cancelled'}
    assert bound.submgr.delete_args == []
