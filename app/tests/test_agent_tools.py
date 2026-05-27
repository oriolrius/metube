"""Unit tests for the in-process agent tools.

We exercise the tool functions directly (not through ADK's
`FunctionTool` invocation) so we don't need a live LLM. The
`require_confirmation=True` plumbing is covered separately in
`test_agent_hitl.py`.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any

import pytest

from agent import tools as agent_tools


class _FakeQueueDict:
    def __init__(self, items: dict[str, Any]) -> None:
        self._items = items

    def saved_items(self):
        return self._items.items()


class FakeDQueue:
    def __init__(self) -> None:
        self.queue = _FakeQueueDict({'q1': SimpleNamespace(id='q1', title='Queued one')})
        self.done = _FakeQueueDict({'d1': SimpleNamespace(id='d1', title='Done one')})
        self.pending = _FakeQueueDict({})
        self.add_calls: list[tuple] = []
        self.cancel_args: list[list[str]] = []
        self.clear_args: list[list[str]] = []
        self.start_pending_args: list[Any] = []
        self.cancel_add_called = False

    async def add(self, *args, **kwargs):
        self.add_calls.append((args, kwargs))
        return {'status': 'ok'}

    async def cancel(self, ids):
        self.cancel_args.append(list(ids))
        return {'status': 'ok', 'cancelled': list(ids)}

    async def clear(self, ids):
        self.clear_args.append(list(ids))
        return {'status': 'ok', 'cleared': list(ids)}

    async def start_pending(self, ids):
        self.start_pending_args.append(ids)
        return {'status': 'ok'}

    def cancel_add(self):
        self.cancel_add_called = True


class FakeSubMgr:
    def __init__(self) -> None:
        self._subs = [SimpleNamespace(to_public_dict=lambda: {'id': 's1', 'name': 'sub1'})]
        self.add_kwargs: dict[str, Any] = {}
        self.update_args: list = []
        self.delete_args: list = []
        self.check_now_args: list = []

    def list_all(self):
        return self._subs

    async def add_subscription(self, url, **kwargs):
        self.add_kwargs = dict(kwargs, url=url)
        return {'status': 'ok', 'id': 'new-sub'}

    async def update_subscription(self, sub_id, changes):
        self.update_args.append((sub_id, changes))
        return {'status': 'ok'}

    async def delete_subscriptions(self, ids):
        self.delete_args.append(list(ids))
        return {'status': 'ok', 'deleted': list(ids)}

    async def check_now(self, ids):
        self.check_now_args.append(ids)
        return {'status': 'ok'}


class FakeConfig:
    def __init__(self, cookies_path: str) -> None:
        self.YTDL_OPTIONS_PRESETS = {'audio': {}, 'video': {}}
        self.YTDL_OPTIONS: dict = {}
        self._cookies_path = cookies_path

    def frontend_safe(self) -> dict:
        return {'AGENT_ENABLED': True}

    def set_runtime_override(self, key, value):
        self.YTDL_OPTIONS[key] = value

    def remove_runtime_override(self, key):
        self.YTDL_OPTIONS.pop(key, None)

    def load_ytdl_options(self):
        return True, ''


@pytest.fixture
def bound_tools(tmp_path):
    cookies_path = str(tmp_path / 'cookies.txt')
    dq = FakeDQueue()
    sm = FakeSubMgr()
    cfg = FakeConfig(cookies_path)
    agent_tools.bind(
        dqueue=dq, submgr=sm, config=cfg,
        cookies_path=cookies_path, live_state=None,
    )
    return SimpleNamespace(dqueue=dq, submgr=sm, config=cfg, cookies_path=cookies_path)


async def test_get_version(bound_tools):
    out = await agent_tools.get_version()
    assert 'yt_dlp_version' in out
    assert out['config'] == {'AGENT_ENABLED': True}


async def test_list_presets_sorted(bound_tools):
    out = await agent_tools.list_presets()
    assert out == {'presets': ['audio', 'video']}


async def test_get_history_groups(bound_tools):
    out = await agent_tools.get_history()
    assert {h['id'] for h in out['queue']} == {'q1'}
    assert {h['id'] for h in out['done']} == {'d1'}
    assert out['pending'] == []


async def test_add_download_passes_positional_args(bound_tools):
    out = await agent_tools.add_download(url='https://x', quality='1080', format='mp4')
    assert out == {'status': 'ok'}
    args, _ = bound_tools.dqueue.add_calls[0]
    assert args[0] == 'https://x'
    assert args[3] == 'mp4'   # format
    assert args[4] == '1080'  # quality


async def test_cancel_add(bound_tools):
    out = await agent_tools.cancel_add()
    assert out == {'status': 'ok'}
    assert bound_tools.dqueue.cancel_add_called


async def test_start_downloads(bound_tools):
    out = await agent_tools.start_downloads(['a', 'b'])
    assert out == {'status': 'ok'}
    assert bound_tools.dqueue.start_pending_args == [['a', 'b']]


async def test_list_subscriptions(bound_tools):
    out = await agent_tools.list_subscriptions()
    assert out == {'subscriptions': [{'id': 's1', 'name': 'sub1'}]}


async def test_subscribe_forwards(bound_tools):
    out = await agent_tools.subscribe(url='https://yt', title_regex='foo')
    assert out['id'] == 'new-sub'
    assert bound_tools.submgr.add_kwargs['url'] == 'https://yt'
    assert bound_tools.submgr.add_kwargs['title_regex'] == 'foo'


async def test_update_subscription_filters_none(bound_tools):
    out = await agent_tools.update_subscription(id='s1', enabled=True)
    assert out == {'status': 'ok'}
    assert bound_tools.submgr.update_args == [('s1', {'enabled': True})]


async def test_update_subscription_rejects_empty(bound_tools):
    out = await agent_tools.update_subscription(id='s1')
    assert out['status'] == 'error'


async def test_check_subscriptions_now(bound_tools):
    out = await agent_tools.check_subscriptions_now(['s1'])
    assert out == {'status': 'ok'}
    assert bound_tools.submgr.check_now_args == [['s1']]


async def test_upload_and_delete_cookies(bound_tools):
    content = b'# Netscape HTTP Cookie File\n'
    b64 = base64.b64encode(content).decode()
    out = await agent_tools.upload_cookies(b64)
    assert out['status'] == 'ok'
    assert bound_tools.config.YTDL_OPTIONS.get('cookiefile') == bound_tools.cookies_path

    status = await agent_tools.cookie_status()
    assert status['has_cookies'] is True

    deleted = await agent_tools.delete_cookies()
    assert deleted['status'] == 'ok'
    assert 'cookiefile' not in bound_tools.config.YTDL_OPTIONS


async def test_upload_cookies_rejects_oversize(bound_tools):
    huge = base64.b64encode(b'x' * 1_000_001).decode()
    out = await agent_tools.upload_cookies(huge)
    assert out['status'] == 'error'


async def test_upload_cookies_rejects_bad_b64(bound_tools):
    out = await agent_tools.upload_cookies('not!!!base64')
    assert out['status'] == 'error'
