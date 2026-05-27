"""Register agent routes onto MeTube's aiohttp app + RouteTableDef."""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from . import build as agent_build
from . import config as agent_config
from . import sse as agent_sse
from . import tools as agent_tools

log = logging.getLogger(__name__)


def register_agent_routes(
    app: web.Application,
    routes: web.RouteTableDef,
    *,
    url_prefix: str,
    dqueue: Any,
    submgr: Any,
    config: Any,
) -> None:
    """Wire the agent into MeTube. Call before `app.add_routes(routes)`.

    `url_prefix` must include the trailing slash (matches MeTube's own
    `config.URL_PREFIX` handling).
    """
    if not agent_config.AGENT_ENABLED:
        log.info('Agent disabled (AGENT_ENABLED is false). Skipping routes.')
        return

    if not agent_config.LITELLM_API_KEY:
        log.error('AGENT_ENABLED is true but LITELLM_API_KEY is empty. Refusing to start agent.')
        return

    agent_tools.bind(dqueue=dqueue, submgr=submgr, config=config)
    agent = agent_build.build_agent()
    runtime = agent_sse.AgentRuntime(agent)
    app['agent_runtime'] = runtime

    routes.post(url_prefix + 'agent/chat')(agent_sse.chat_handler)
    log.info('Agent routes registered at %sagent/chat', url_prefix)
