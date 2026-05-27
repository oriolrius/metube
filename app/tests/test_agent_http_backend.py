"""HttpBackend talks to a stub aiohttp server, exercising every method."""

from __future__ import annotations

import base64

import pytest
from aiohttp import web

from agent.backend import HttpBackend


def _make_handler(reply, recorder):
    async def handler(request: web.Request):
        recorder.setdefault('calls', []).append({
            'method': request.method,
            'path': request.path,
            'body': await request.json() if request.body_exists and request.content_type == 'application/json' else None,
        })
        if callable(reply):
            data = reply(request)
            if data is None:
                return web.json_response({'status': 'ok'})
            return web.json_response(data)
        return web.json_response(reply)
    return handler


async def _upload_cookies(request: web.Request):
    reader = await request.multipart()
    field = await reader.next()
    content = await field.read()
    request.app['recorder']['cookie_bytes'] = len(content)
    return web.json_response({'status': 'ok', 'bytes': len(content)})


def _stub_app(recorder: dict) -> web.Application:
    app = web.Application()
    app['recorder'] = recorder
    h = _make_handler
    app.router.add_get('/version', h({'yt-dlp': '2026.03.17', 'version': 'test'}, recorder))
    app.router.add_get('/presets', h({'presets': ['audio']}, recorder))
    app.router.add_get('/history', h({'queue': [], 'done': [{'id': 'd1'}], 'pending': []}, recorder))
    app.router.add_post('/add', h({'status': 'ok'}, recorder))
    app.router.add_post('/cancel-add', h({'status': 'ok'}, recorder))
    app.router.add_post('/start', h({'status': 'ok'}, recorder))
    app.router.add_post('/delete', h({'status': 'ok', 'deleted': True}, recorder))
    app.router.add_get('/subscriptions', h([{'id': 's1', 'name': 'sub'}], recorder))
    app.router.add_post('/subscribe', h({'status': 'ok', 'id': 's2'}, recorder))
    app.router.add_post('/subscriptions/update', h({'status': 'ok'}, recorder))
    app.router.add_post('/subscriptions/delete', h({'status': 'ok'}, recorder))
    app.router.add_post('/subscriptions/check', h({'status': 'ok'}, recorder))
    app.router.add_post('/upload-cookies', _upload_cookies)
    app.router.add_post('/delete-cookies', h({'status': 'ok'}, recorder))
    app.router.add_get('/cookie-status', h({'has_cookies': False}, recorder))
    return app


@pytest.fixture
async def stub(aiohttp_server):
    recorder = {'calls': []}
    server = await aiohttp_server(_stub_app(recorder))
    base = f'http://127.0.0.1:{server.port}/'
    yield HttpBackend(base), recorder


async def test_get_version(stub):
    backend, rec = stub
    out = await backend.get_version()
    assert out == {'yt-dlp': '2026.03.17', 'version': 'test'}
    assert rec['calls'][0]['method'] == 'GET' and rec['calls'][0]['path'] == '/version'


async def test_list_presets(stub):
    backend, _ = stub
    out = await backend.list_presets()
    assert out == {'presets': ['audio']}


async def test_get_history(stub):
    backend, _ = stub
    out = await backend.get_history()
    assert len(out['done']) == 1


async def test_add_download_forwards_payload(stub):
    backend, rec = stub
    out = await backend.add_download({
        'url': 'https://x', 'quality': 'best', 'format': 'any',
        'download_type': 'video', 'codec': 'auto', 'folder': '',
        'custom_name_prefix': '', 'playlist_item_limit': 0,
        'auto_start': True, 'split_by_chapters': False,
        'chapter_template': None, 'subtitle_language': None,
        'subtitle_mode': None, 'ytdl_options_presets': None,
        'clip_start': None, 'clip_end': None,
    })
    assert out == {'status': 'ok'}
    body = rec['calls'][-1]['body']
    assert body['url'] == 'https://x' and body['quality'] == 'best'


async def test_start_and_delete(stub):
    backend, rec = stub
    await backend.start_downloads(['a'])
    await backend.delete_downloads(['b'], 'done')
    assert rec['calls'][-2] == {'method': 'POST', 'path': '/start', 'body': {'ids': ['a']}}
    assert rec['calls'][-1] == {'method': 'POST', 'path': '/delete', 'body': {'ids': ['b'], 'where': 'done'}}


async def test_subscriptions_roundtrip(stub):
    backend, rec = stub
    subs = await backend.list_subscriptions()
    assert subs == {'subscriptions': [{'id': 's1', 'name': 'sub'}]}
    await backend.subscribe({'url': 'https://y', 'title_regex': 'foo'})
    await backend.update_subscription('s1', {'enabled': True})
    await backend.delete_subscriptions(['s1'])
    await backend.check_subscriptions_now(['s1'])
    paths = [c['path'] for c in rec['calls']]
    assert '/subscriptions/update' in paths
    assert '/subscriptions/delete' in paths
    assert '/subscriptions/check' in paths


async def test_cookies_upload_multipart(stub):
    backend, rec = stub
    content = b'# cookies'
    out = await backend.upload_cookies(base64.b64encode(content).decode())
    assert out['status'] == 'ok'
    assert rec['cookie_bytes'] == len(content)


async def test_cookies_upload_rejects_oversize(stub):
    backend, _ = stub
    huge = base64.b64encode(b'x' * 1_000_001).decode()
    out = await backend.upload_cookies(huge)
    assert out['status'] == 'error'


async def test_get_live_state_falls_back_to_history(stub):
    backend, _ = stub
    out = await backend.get_live_state()
    assert 'snapshot' in out
    assert out['snapshot']['done'] and out['snapshot']['done'][0]['id'] == 'd1'
    assert 'note' in out
