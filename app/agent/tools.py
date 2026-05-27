"""In-process FunctionTools for the MeTube agent.

Each tool calls MeTube's Python objects directly (D1 in PLAN.md). The
backing references (`dqueue`, `submgr`, `config`) are injected via
`bind(...)` at startup so this module has no import-time dependency on
`app.main`.
"""

from __future__ import annotations

from typing import Any

from google.adk.tools import FunctionTool

# Backing objects, populated by `bind()` from app.main during startup.
_dqueue: Any = None
_submgr: Any = None
_config: Any = None


def bind(*, dqueue: Any, submgr: Any, config: Any) -> None:
    """Inject MeTube's runtime objects into this module."""
    global _dqueue, _submgr, _config
    _dqueue = dqueue
    _submgr = submgr
    _config = config


async def get_version() -> dict:
    """Return the MeTube version, yt-dlp version, and config snapshot."""
    import yt_dlp.version  # local import to avoid eager dep at module load
    return {
        'yt_dlp_version': yt_dlp.version.__version__,
        'config': _config.frontend_safe() if _config else {},
    }


def build_tools() -> list[FunctionTool]:
    """Return the agent's FunctionTool list.

    Step 1 ships only `get_version` for the end-to-end smoke test;
    subsequent steps add downloads, subscriptions, cookies, and HITL.
    """
    return [
        FunctionTool(get_version),
    ]
