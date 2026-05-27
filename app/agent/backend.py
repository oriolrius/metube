"""Two backends for the agent tools.

`InProcessBackend` is used by the embedded agent inside MeTube's aiohttp
server — it calls `dqueue` / `submgr` / `config` directly so latency is
zero and the `Notifier` fires socket.io events to connected UI clients.

`HttpBackend` is used by the A2A sidecar (a separate Python process).
It calls MeTube's HTTP API on `localhost:8081`, which means every
mutation goes through MeTube's main process — same code path as the
UI, so the UI's socket.io connection sees `added` / `completed` /
`canceled` events live.

Both backends expose the same async interface. `tools.py` is unchanged
between processes; only the binding strategy differs.
"""

from __future__ import annotations

import base64
import os
from typing import Any, Optional, Protocol


class Backend(Protocol):
    async def get_version(self) -> dict: ...
    async def list_presets(self) -> dict: ...
    async def get_history(self) -> dict: ...
    async def add_download(self, payload: dict) -> dict: ...
    async def cancel_add(self) -> dict: ...
    async def start_downloads(self, ids: list[str]) -> dict: ...
    async def delete_downloads(self, ids: list[str], where: str) -> dict: ...
    async def list_subscriptions(self) -> dict: ...
    async def subscribe(self, payload: dict) -> dict: ...
    async def update_subscription(self, sub_id: str, changes: dict) -> dict: ...
    async def delete_subscriptions(self, ids: list[str]) -> dict: ...
    async def check_subscriptions_now(self, ids: Optional[list[str]]) -> dict: ...
    async def upload_cookies(self, content_b64: str) -> dict: ...
    async def delete_cookies(self) -> dict: ...
    async def cookie_status(self) -> dict: ...
    async def get_live_state(self) -> dict: ...


# ─── In-process backend (used by embedded agent in app/main.py) ──────

class InProcessBackend:
    """Direct calls to MeTube's Python objects."""

    def __init__(self, *, dqueue: Any, submgr: Any, config: Any,
                 cookies_path: str, live_state: Any = None) -> None:
        self.dqueue = dqueue
        self.submgr = submgr
        self.config = config
        self.cookies_path = cookies_path
        self.live_state = live_state

    async def get_version(self) -> dict:
        import yt_dlp.version
        return {
            'metube_version': os.environ.get('METUBE_VERSION', 'dev'),
            'yt_dlp_version': yt_dlp.version.__version__,
            'config': self.config.frontend_safe(),
        }

    async def list_presets(self) -> dict:
        return {'presets': sorted(self.config.YTDL_OPTIONS_PRESETS.keys())}

    async def get_history(self) -> dict:
        out: dict = {'done': [], 'queue': [], 'pending': []}
        for _, v in self.dqueue.queue.saved_items():
            out['queue'].append(v.__dict__ if hasattr(v, '__dict__') else v)
        for _, v in self.dqueue.done.saved_items():
            out['done'].append(v.__dict__ if hasattr(v, '__dict__') else v)
        for _, v in self.dqueue.pending.saved_items():
            out['pending'].append(v.__dict__ if hasattr(v, '__dict__') else v)
        return out

    async def add_download(self, payload: dict) -> dict:
        return await self.dqueue.add(
            payload['url'], payload['download_type'], payload['codec'],
            payload['format'], payload['quality'], payload['folder'],
            payload['custom_name_prefix'], payload['playlist_item_limit'],
            payload['auto_start'], payload['split_by_chapters'],
            payload['chapter_template'],
            payload['subtitle_language'] or 'en',
            payload['subtitle_mode'] or 'prefer_manual',
            payload['ytdl_options_presets'], None,
            payload['clip_start'], payload['clip_end'],
        )

    async def cancel_add(self) -> dict:
        self.dqueue.cancel_add()
        return {'status': 'ok'}

    async def start_downloads(self, ids: list[str]) -> dict:
        return await self.dqueue.start_pending(ids)

    async def delete_downloads(self, ids: list[str], where: str) -> dict:
        if where == 'queue':
            return await self.dqueue.cancel(ids)
        return await self.dqueue.clear(ids)

    async def list_subscriptions(self) -> dict:
        return {'subscriptions': [s.to_public_dict() for s in self.submgr.list_all()]}

    async def subscribe(self, payload: dict) -> dict:
        kwargs = {k: v for k, v in payload.items() if k != 'url'}
        # Normalise default-bearing fields like the HTTP path does.
        kwargs.setdefault('subtitle_language', 'en')
        kwargs.setdefault('subtitle_mode', 'prefer_manual')
        return await self.submgr.add_subscription(payload['url'], **kwargs)

    async def update_subscription(self, sub_id: str, changes: dict) -> dict:
        return await self.submgr.update_subscription(str(sub_id), changes)

    async def delete_subscriptions(self, ids: list[str]) -> dict:
        return await self.submgr.delete_subscriptions([str(i) for i in ids])

    async def check_subscriptions_now(self, ids: Optional[list[str]]) -> dict:
        return await self.submgr.check_now([str(i) for i in ids] if ids else None)

    async def upload_cookies(self, content_b64: str) -> dict:
        try:
            content = base64.b64decode(content_b64, validate=True)
        except Exception as exc:
            return {'status': 'error', 'msg': f'invalid base64: {exc}'}
        if len(content) > 1_000_000:
            return {'status': 'error', 'msg': 'Cookie file too large (max 1MB)'}
        tmp = f'{self.cookies_path}.tmp'
        with open(tmp, 'wb') as f:
            f.write(content)
        os.replace(tmp, self.cookies_path)
        self.config.set_runtime_override('cookiefile', self.cookies_path)
        return {'status': 'ok', 'bytes': len(content)}

    async def delete_cookies(self) -> dict:
        if not os.path.exists(self.cookies_path):
            return {'status': 'error', 'msg': 'no uploaded cookies'}
        os.remove(self.cookies_path)
        self.config.remove_runtime_override('cookiefile')
        ok, msg = self.config.load_ytdl_options()
        if not ok:
            return {'status': 'error', 'msg': f'reload failed: {msg}'}
        return {'status': 'ok'}

    async def cookie_status(self) -> dict:
        configured = self.config.YTDL_OPTIONS.get('cookiefile')
        has_configured = isinstance(configured, str) and os.path.exists(configured)
        has_uploaded = os.path.exists(self.cookies_path)
        return {'status': 'ok', 'has_cookies': has_uploaded or has_configured}

    async def get_live_state(self) -> dict:
        if self.live_state is None:
            return {'snapshot': None}
        return self.live_state.snapshot()


# ─── HTTP loopback backend (used by the A2A sidecar) ─────────────────

class HttpBackend:
    """Calls MeTube's REST API on `base_url`. Same code path as the UI."""

    def __init__(self, base_url: str) -> None:
        # base_url like "http://127.0.0.1:8081/" — must include URL_PREFIX
        # if MeTube is mounted under one. Trailing slash required.
        if not base_url.endswith('/'):
            base_url += '/'
        self.base_url = base_url
        self._session = None  # lazy aiohttp.ClientSession

    async def _client(self):
        import aiohttp
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _get(self, path: str) -> Any:
        sess = await self._client()
        async with sess.get(self.base_url + path) as r:
            return await r.json(content_type=None)

    async def _post(self, path: str, body: Any = None) -> Any:
        sess = await self._client()
        async with sess.post(self.base_url + path, json=body) as r:
            return await r.json(content_type=None)

    async def get_version(self) -> dict:
        return await self._get('version')

    async def list_presets(self) -> dict:
        return await self._get('presets')

    async def get_history(self) -> dict:
        return await self._get('history')

    async def add_download(self, payload: dict) -> dict:
        # MeTube's POST /add accepts the same parse_download_options shape.
        return await self._post('add', payload)

    async def cancel_add(self) -> dict:
        return await self._post('cancel-add')

    async def start_downloads(self, ids: list[str]) -> dict:
        return await self._post('start', {'ids': ids})

    async def delete_downloads(self, ids: list[str], where: str) -> dict:
        return await self._post('delete', {'ids': ids, 'where': where})

    async def list_subscriptions(self) -> dict:
        data = await self._get('subscriptions')
        return {'subscriptions': data}

    async def subscribe(self, payload: dict) -> dict:
        return await self._post('subscribe', payload)

    async def update_subscription(self, sub_id: str, changes: dict) -> dict:
        body = {'id': str(sub_id), **changes}
        return await self._post('subscriptions/update', body)

    async def delete_subscriptions(self, ids: list[str]) -> dict:
        return await self._post('subscriptions/delete', {'ids': ids})

    async def check_subscriptions_now(self, ids: Optional[list[str]]) -> dict:
        return await self._post('subscriptions/check', {'ids': ids} if ids else {})

    async def upload_cookies(self, content_b64: str) -> dict:
        import aiohttp
        try:
            content = base64.b64decode(content_b64, validate=True)
        except Exception as exc:
            return {'status': 'error', 'msg': f'invalid base64: {exc}'}
        if len(content) > 1_000_000:
            return {'status': 'error', 'msg': 'Cookie file too large (max 1MB)'}
        form = aiohttp.FormData()
        form.add_field('cookies', content, filename='cookies.txt',
                       content_type='text/plain')
        sess = await self._client()
        async with sess.post(self.base_url + 'upload-cookies', data=form) as r:
            return await r.json(content_type=None)

    async def delete_cookies(self) -> dict:
        return await self._post('delete-cookies')

    async def cookie_status(self) -> dict:
        return await self._get('cookie-status')

    async def get_live_state(self) -> dict:
        # The Notifier hook only exists in the embedded process. From a
        # remote caller we can only return the snapshot derivable from
        # /history. Document the limitation in the response.
        h = await self.get_history()
        return {
            'snapshot': {
                'queue': h.get('queue', []),
                'done': h.get('done', []),
                'pending': h.get('pending', []),
            },
            'note': 'HTTP backend: only persisted state is visible (no live events).',
        }
