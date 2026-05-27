"""In-process FunctionTools for the MeTube agent.

Each tool calls MeTube's Python objects directly (D1 in PLAN.md).
Backing references are injected via `bind(...)` at startup so this
module has no import-time dependency on `app.main`.

HITL (D4): only `delete_downloads` and `delete_subscriptions` use
`require_confirmation=True`. Inside those tools we call
`tool_context.request_confirmation(...)` on the first invocation and
proceed once `tool_context.tool_confirmation` is set on resume.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from google.adk.tools import FunctionTool, ToolContext

log = logging.getLogger(__name__)

# Backing objects, populated by `bind()` from app.main during startup.
_dqueue: Any = None
_submgr: Any = None
_config: Any = None
_cookies_path: Optional[str] = None
_live_state: Any = None


def bind(*, dqueue: Any, submgr: Any, config: Any, cookies_path: str,
         live_state: Any = None) -> None:
    """Inject MeTube's runtime objects into this module."""
    global _dqueue, _submgr, _config, _cookies_path, _live_state
    _dqueue = dqueue
    _submgr = submgr
    _config = config
    _cookies_path = cookies_path
    _live_state = live_state


# ─── Downloads ────────────────────────────────────────────────────────

async def get_version() -> dict:
    """Return the MeTube version, yt-dlp version, and frontend-safe config snapshot."""
    import yt_dlp.version
    return {
        'metube_version': os.environ.get('METUBE_VERSION', 'dev'),
        'yt_dlp_version': yt_dlp.version.__version__,
        'config': _config.frontend_safe() if _config else {},
    }


async def list_presets() -> dict:
    """List the configured yt-dlp option preset names."""
    return {'presets': sorted(_config.YTDL_OPTIONS_PRESETS.keys())}


async def get_history() -> dict:
    """Return all downloads grouped by state: done, queue, pending."""
    history: dict = {'done': [], 'queue': [], 'pending': []}
    for _, v in _dqueue.queue.saved_items():
        history['queue'].append(v.__dict__ if hasattr(v, '__dict__') else v)
    for _, v in _dqueue.done.saved_items():
        history['done'].append(v.__dict__ if hasattr(v, '__dict__') else v)
    for _, v in _dqueue.pending.saved_items():
        history['pending'].append(v.__dict__ if hasattr(v, '__dict__') else v)
    return history


async def add_download(
    url: str,
    quality: str = 'best',
    format: str = 'any',
    download_type: str = 'video',
    codec: str = 'auto',
    folder: str = '',
    custom_name_prefix: str = '',
    playlist_item_limit: int = 0,
    auto_start: bool = True,
    split_by_chapters: bool = False,
    chapter_template: Optional[str] = None,
    subtitle_language: Optional[str] = None,
    subtitle_mode: Optional[str] = None,
    ytdl_options_presets: Optional[list[str]] = None,
    clip_start: Optional[str] = None,
    clip_end: Optional[str] = None,
) -> dict:
    """Queue a download. See MeTube's POST /add for parameter semantics."""
    return await _dqueue.add(
        url, download_type, codec, format, quality, folder, custom_name_prefix,
        playlist_item_limit, auto_start, split_by_chapters, chapter_template,
        subtitle_language or 'en', subtitle_mode or 'prefer_manual',
        ytdl_options_presets, None, clip_start, clip_end,
    )


async def cancel_add() -> dict:
    """Cancel an in-flight `add_download` resolution (does not stop running downloads)."""
    _dqueue.cancel_add()
    return {'status': 'ok'}


async def start_downloads(ids: list[str]) -> dict:
    """Start one or more pending downloads."""
    return await _dqueue.start_pending(ids)


async def delete_downloads(
    ids: list[str],
    where: str,
    tool_context: ToolContext,
) -> dict:
    """Delete downloads. `where` must be 'queue' (cancel) or 'done' (clear).

    Destructive: confirmation required on first call.
    """
    if where not in ('queue', 'done'):
        return {'status': 'error', 'msg': "where must be 'queue' or 'done'"}

    if tool_context.tool_confirmation is None:
        tool_context.request_confirmation(
            hint=f"Delete {len(ids)} item(s) from '{where}'?",
            payload={'ids': ids, 'where': where},
        )
        return {'status': 'awaiting_confirmation'}

    # On resume, allow the UI to amend ids/where via payload.
    payload = dict(tool_context.tool_confirmation.payload or {})
    final_ids = payload.get('ids', ids)
    final_where = payload.get('where', where)

    if not getattr(tool_context.tool_confirmation, 'confirmed', True):
        return {'status': 'cancelled'}

    if final_where == 'queue':
        return await _dqueue.cancel(final_ids)
    return await _dqueue.clear(final_ids)


# ─── Subscriptions ────────────────────────────────────────────────────

async def list_subscriptions() -> dict:
    """List all subscriptions."""
    return {'subscriptions': [s.to_public_dict() for s in _submgr.list_all()]}


async def subscribe(
    url: str,
    check_interval_minutes: int = 60,
    quality: str = 'best',
    format: str = 'any',
    download_type: str = 'video',
    codec: str = 'auto',
    folder: str = '',
    custom_name_prefix: str = '',
    auto_start: bool = True,
    playlist_item_limit: int = 0,
    split_by_chapters: bool = False,
    chapter_template: Optional[str] = None,
    subtitle_language: Optional[str] = None,
    subtitle_mode: Optional[str] = None,
    ytdl_options_presets: Optional[list[str]] = None,
    title_regex: Optional[str] = None,
    skip_subscriber_only: bool = False,
) -> dict:
    """Create a subscription. Mirrors MeTube's POST /subscribe."""
    return await _submgr.add_subscription(
        url,
        check_interval_minutes=check_interval_minutes,
        download_type=download_type,
        codec=codec,
        format=format,
        quality=quality,
        folder=folder,
        custom_name_prefix=custom_name_prefix,
        auto_start=auto_start,
        playlist_item_limit=playlist_item_limit,
        split_by_chapters=split_by_chapters,
        chapter_template=chapter_template,
        subtitle_language=subtitle_language or 'en',
        subtitle_mode=subtitle_mode or 'prefer_manual',
        ytdl_options_presets=ytdl_options_presets,
        ytdl_options_overrides=None,
        title_regex=title_regex,
        skip_subscriber_only=skip_subscriber_only,
    )


async def update_subscription(
    id: str,
    enabled: Optional[bool] = None,
    check_interval_minutes: Optional[int] = None,
    name: Optional[str] = None,
    title_regex: Optional[str] = None,
    skip_subscriber_only: Optional[bool] = None,
) -> dict:
    """Update a subscription's mutable fields. Pass only the fields to change."""
    changes = {
        k: v for k, v in {
            'enabled': enabled,
            'check_interval_minutes': check_interval_minutes,
            'name': name,
            'title_regex': title_regex,
            'skip_subscriber_only': skip_subscriber_only,
        }.items() if v is not None
    }
    if not changes:
        return {'status': 'error', 'msg': 'no fields to update'}
    return await _submgr.update_subscription(str(id), changes)


async def delete_subscriptions(
    ids: list[str],
    tool_context: ToolContext,
) -> dict:
    """Delete subscriptions by id. Destructive: confirmation required."""
    if tool_context.tool_confirmation is None:
        tool_context.request_confirmation(
            hint=f"Delete {len(ids)} subscription(s)?",
            payload={'ids': ids},
        )
        return {'status': 'awaiting_confirmation'}

    payload = dict(tool_context.tool_confirmation.payload or {})
    final_ids = payload.get('ids', ids)

    if not getattr(tool_context.tool_confirmation, 'confirmed', True):
        return {'status': 'cancelled'}

    return await _submgr.delete_subscriptions([str(i) for i in final_ids])


async def check_subscriptions_now(ids: Optional[list[str]] = None) -> dict:
    """Trigger an immediate check for one or more subscriptions (or all when ids is None)."""
    return await _submgr.check_now([str(i) for i in ids] if ids else None)


# ─── Cookies ──────────────────────────────────────────────────────────

async def upload_cookies(content_b64: str) -> dict:
    """Upload a Netscape cookies.txt file (base64-encoded). 1 MB cap."""
    import base64
    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception as exc:
        return {'status': 'error', 'msg': f'invalid base64: {exc}'}
    if len(content) > 1_000_000:
        return {'status': 'error', 'msg': 'Cookie file too large (max 1MB)'}
    tmp = f'{_cookies_path}.tmp'
    with open(tmp, 'wb') as f:
        f.write(content)
    os.replace(tmp, _cookies_path)
    _config.set_runtime_override('cookiefile', _cookies_path)
    return {'status': 'ok', 'bytes': len(content)}


async def delete_cookies() -> dict:
    """Remove the uploaded cookies file (if any)."""
    if not os.path.exists(_cookies_path):
        return {'status': 'error', 'msg': 'no uploaded cookies'}
    os.remove(_cookies_path)
    _config.remove_runtime_override('cookiefile')
    ok, msg = _config.load_ytdl_options()
    if not ok:
        return {'status': 'error', 'msg': f'reload failed: {msg}'}
    return {'status': 'ok'}


async def cookie_status() -> dict:
    """Whether MeTube currently has cookies configured."""
    configured = _config.YTDL_OPTIONS.get('cookiefile')
    has_configured = isinstance(configured, str) and os.path.exists(configured)
    has_uploaded = os.path.exists(_cookies_path) if _cookies_path else False
    return {'status': 'ok', 'has_cookies': has_uploaded or has_configured}


# ─── Live state ───────────────────────────────────────────────────────

async def get_live_state() -> dict:
    """Snapshot of in-flight download events maintained by the Notifier hook."""
    if _live_state is None:
        return {'snapshot': None}
    return _live_state.snapshot()


# ─── Tool registry ────────────────────────────────────────────────────

def build_tools() -> list[FunctionTool]:
    """Construct the FunctionTool list passed to the LlmAgent."""
    return [
        FunctionTool(get_version),
        FunctionTool(list_presets),
        FunctionTool(get_history),
        FunctionTool(add_download),
        FunctionTool(cancel_add),
        FunctionTool(start_downloads),
        # delete_downloads handles confirmation manually so we can show
        # a custom hint (item count, target queue).
        FunctionTool(delete_downloads),
        FunctionTool(list_subscriptions),
        FunctionTool(subscribe),
        FunctionTool(update_subscription),
        FunctionTool(delete_subscriptions),
        FunctionTool(check_subscriptions_now),
        FunctionTool(upload_cookies),
        FunctionTool(delete_cookies),
        FunctionTool(cookie_status),
        FunctionTool(get_live_state),
    ]
