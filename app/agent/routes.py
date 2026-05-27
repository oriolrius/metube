"""Register agent routes onto MeTube's aiohttp app + RouteTableDef."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from . import build as agent_build
from . import config as agent_config
from . import sse as agent_sse
from . import tools as agent_tools
from . import tracing as agent_tracing
from .backend import InProcessBackend
from .live_state import AgentLiveState, wrap_notifier

log = logging.getLogger(__name__)


def register_agent_routes(
    app: web.Application,
    routes: web.RouteTableDef,
    *,
    url_prefix: str,
    dqueue: Any,
    submgr: Any,
    config: Any,
    cookies_path: str,
    notifier: Any,
) -> bool:
    """Wire the agent into MeTube. Call before `app.add_routes(routes)`.

    `url_prefix` must include the trailing slash. Returns True if the
    agent was registered, False if it stayed disabled.
    """
    if not agent_config.AGENT_ENABLED:
        log.info('Agent disabled (AGENT_ENABLED=false). Skipping routes.')
        return False

    if not agent_config.LITELLM_API_KEY:
        log.error('AGENT_ENABLED is true but LITELLM_API_KEY is empty. Refusing to start agent.')
        return False

    agent_tracing.setup_tracing(process_role='embedded')

    state = AgentLiveState()
    wrap_notifier(notifier, state)

    agent_tools.bind(InProcessBackend(
        dqueue=dqueue, submgr=submgr, config=config,
        cookies_path=cookies_path, live_state=state,
    ))
    agent = agent_build.build_agent()
    runtime = agent_sse.AgentRuntime(agent)
    app['agent_runtime'] = runtime

    routes.post(url_prefix + 'agent/chat')(agent_sse.chat_handler)
    routes.post(url_prefix + 'agent/confirm')(agent_sse.confirm_handler)
    log.info('Agent routes registered at %sagent/{chat,confirm}', url_prefix)
    return True
