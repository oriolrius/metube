"""In-process FunctionTools for the MeTube agent.

Tool signatures define the agent's surface. Each tool delegates to the
module-level `_backend` (set by `bind()`), which is either an
`InProcessBackend` (embedded in MeTube's aiohttp server) or an
`HttpBackend` (used by the A2A sidecar — calls MeTube's REST API on
localhost so the UI sees socket.io events live).

HITL (D4): delete_downloads / delete_subscriptions call
`tool_context.request_confirmation` on the first invocation and
proceed once `tool_context.tool_confirmation` is set on resume.
"""

from __future__ import annotations

from typing import Optional

from google.adk.tools import FunctionTool, ToolContext

from .backend import Backend

# Backend, populated by `bind()`.
_backend: Optional[Backend] = None


def bind(backend: Backend) -> None:
    global _backend
    _backend = backend


# ─── Downloads ────────────────────────────────────────────────────────

async def get_version() -> dict:
    """Return the MeTube version, yt-dlp version, and frontend-safe config snapshot."""
    return await _backend.get_version()


async def list_presets() -> dict:
    """List the configured yt-dlp option preset names."""
    return await _backend.list_presets()


async def get_history() -> dict:
    """Return all downloads grouped by state: done, queue, pending."""
    return await _backend.get_history()


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
    return await _backend.add_download({
        'url': url, 'quality': quality, 'format': format,
        'download_type': download_type, 'codec': codec,
        'folder': folder, 'custom_name_prefix': custom_name_prefix,
        'playlist_item_limit': playlist_item_limit,
        'auto_start': auto_start, 'split_by_chapters': split_by_chapters,
        'chapter_template': chapter_template,
        'subtitle_language': subtitle_language,
        'subtitle_mode': subtitle_mode,
        'ytdl_options_presets': ytdl_options_presets,
        'clip_start': clip_start, 'clip_end': clip_end,
    })


async def cancel_add() -> dict:
    """Cancel an in-flight `add_download` resolution (does not stop running downloads)."""
    return await _backend.cancel_add()


async def start_downloads(ids: list[str]) -> dict:
    """Start one or more pending downloads.

    Each item in `ids` must be the download's **`url` field**, not the
    `id` field — MeTube's persistent queue is keyed by URL.
    """
    return await _backend.start_downloads(ids)


async def delete_downloads(
    ids: list[str],
    where: str,
    tool_context: ToolContext,
) -> dict:
    """Delete downloads. `where` must be 'queue' (cancel) or 'done' (clear).

    IMPORTANT: each item in `ids` must be the download's **`url` field**
    (e.g. `https://www.youtube.com/watch?v=dQw4w9WgXcQ`), NOT the `id`
    field. MeTube's persistent queue is keyed by URL; passing a video
    id silently no-ops with a "non-existent download" warning. Pull
    the `url` from `get_history()` and pass it back here.

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

    payload = dict(tool_context.tool_confirmation.payload or {})
    final_ids = payload.get('ids', ids)
    final_where = payload.get('where', where)

    if not getattr(tool_context.tool_confirmation, 'confirmed', True):
        return {'status': 'cancelled'}

    return await _backend.delete_downloads(final_ids, final_where)


# ─── Subscriptions ────────────────────────────────────────────────────

async def list_subscriptions() -> dict:
    """List all subscriptions."""
    return await _backend.list_subscriptions()


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
    return await _backend.subscribe({
        'url': url,
        'check_interval_minutes': check_interval_minutes,
        'download_type': download_type, 'codec': codec,
        'format': format, 'quality': quality,
        'folder': folder, 'custom_name_prefix': custom_name_prefix,
        'auto_start': auto_start, 'playlist_item_limit': playlist_item_limit,
        'split_by_chapters': split_by_chapters,
        'chapter_template': chapter_template,
        'subtitle_language': subtitle_language,
        'subtitle_mode': subtitle_mode,
        'ytdl_options_presets': ytdl_options_presets,
        'title_regex': title_regex,
        'skip_subscriber_only': skip_subscriber_only,
    })


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
    return await _backend.update_subscription(str(id), changes)


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

    return await _backend.delete_subscriptions(final_ids)


async def check_subscriptions_now(ids: Optional[list[str]] = None) -> dict:
    """Trigger an immediate check for one or more subscriptions (or all when ids is None)."""
    return await _backend.check_subscriptions_now(ids)


# ─── Cookies ──────────────────────────────────────────────────────────

async def upload_cookies(content_b64: str) -> dict:
    """Upload a Netscape cookies.txt file (base64-encoded). 1 MB cap."""
    return await _backend.upload_cookies(content_b64)


async def delete_cookies() -> dict:
    """Remove the uploaded cookies file (if any)."""
    return await _backend.delete_cookies()


async def cookie_status() -> dict:
    """Whether MeTube currently has cookies configured."""
    return await _backend.cookie_status()


# ─── Live state ───────────────────────────────────────────────────────

async def get_live_state() -> dict:
    """Snapshot of in-flight download events."""
    return await _backend.get_live_state()


# ─── Tool registry ────────────────────────────────────────────────────

def build_tools() -> list[FunctionTool]:
    return [
        FunctionTool(get_version),
        FunctionTool(list_presets),
        FunctionTool(get_history),
        FunctionTool(add_download),
        FunctionTool(cancel_add),
        FunctionTool(start_downloads),
        FunctionTool(delete_downloads),       # HITL via request_confirmation
        FunctionTool(list_subscriptions),
        FunctionTool(subscribe),
        FunctionTool(update_subscription),
        FunctionTool(delete_subscriptions),   # HITL via request_confirmation
        FunctionTool(check_subscriptions_now),
        FunctionTool(upload_cookies),
        FunctionTool(delete_cookies),
        FunctionTool(cookie_status),
        FunctionTool(get_live_state),
    ]
