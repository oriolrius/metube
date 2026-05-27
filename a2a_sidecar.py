"""A2A sidecar process — publishes the same MeTube agent over Google's A2A
protocol on a separate port.

Run with:
    uvicorn a2a_sidecar:app --host 0.0.0.0 --port ${AGENT_A2A_PORT:-8082}

Imports MeTube's `dqueue`, `submgr`, `config`, `Notifier`, and
`COOKIES_PATH` from `app.main`, binds them into the agent tools, and
hands the constructed `LlmAgent` to ADK's `to_a2a()`.

D3 caveat: this is a separate Python process from the aiohttp server,
so its in-memory state (ADK session, live_state snapshot, HITL pending
map) is independent of the embedded one.
"""

from __future__ import annotations

import logging
import os
import sys

# Make `app/` importable the same way pytest does
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from google.adk.a2a.utils.agent_to_a2a import to_a2a  # noqa: E402

from agent import build as agent_build  # noqa: E402
from agent import config as agent_config  # noqa: E402
from agent import tools as agent_tools  # noqa: E402
from agent import tracing as agent_tracing  # noqa: E402
from agent.live_state import AgentLiveState, wrap_notifier  # noqa: E402

log = logging.getLogger(__name__)


def _build() -> 'starlette.applications.Starlette':
    if not agent_config.AGENT_ENABLED:
        raise RuntimeError('AGENT_ENABLED=false; refusing to start A2A sidecar.')
    if not agent_config.LITELLM_API_KEY:
        raise RuntimeError('LITELLM_API_KEY missing; refusing to start A2A sidecar.')

    # Importing app.main mounts all of MeTube's routes; we don't run the
    # aiohttp server here, just borrow the constructed objects.
    import main as metube_main  # noqa: F401  (side effects)

    agent_tracing.setup_tracing(process_role='a2a')

    state = AgentLiveState()
    wrap_notifier(metube_main.download_notifier, state)

    agent_tools.bind(
        dqueue=metube_main.dqueue,
        submgr=metube_main.submgr,
        config=metube_main.config,
        cookies_path=metube_main.COOKIES_PATH,
        live_state=state,
    )
    agent = agent_build.build_agent()

    return to_a2a(
        agent,
        host='0.0.0.0',
        port=agent_config.AGENT_A2A_PORT,
    )


# uvicorn imports this module and looks for `app`
app = _build()
