"""A2A sidecar — publishes the MeTube agent over Google's A2A protocol
on a separate uvicorn process. Tools call MeTube's REST API on
`METUBE_INTERNAL_URL` (default `http://127.0.0.1:8081/`) so every
mutation goes through MeTube's main process and the UI's socket.io
connection sees `added` / `updated` / `completed` events live.

Run with:
    uvicorn a2a_sidecar:app --host 0.0.0.0 --port ${AGENT_A2A_PORT:-8082}

This module does NOT import `app.main`. It builds the agent purely from
configuration + the HTTP backend.
"""

from __future__ import annotations

import logging
import os
import sys

# Make `app/` importable for the `agent` package only.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from google.adk.a2a.utils.agent_to_a2a import to_a2a  # noqa: E402

from agent import build as agent_build  # noqa: E402
from agent import config as agent_config  # noqa: E402
from agent import tools as agent_tools  # noqa: E402
from agent import tracing as agent_tracing  # noqa: E402
from agent.backend import HttpBackend  # noqa: E402

log = logging.getLogger(__name__)


def _build():
    if not agent_config.AGENT_ENABLED:
        raise RuntimeError('AGENT_ENABLED=false; refusing to start A2A sidecar.')
    if not agent_config.LITELLM_API_KEY:
        raise RuntimeError('LITELLM_API_KEY missing; refusing to start A2A sidecar.')

    agent_tracing.setup_tracing(process_role='a2a')

    base = os.environ.get('METUBE_INTERNAL_URL', 'http://127.0.0.1:8081/')
    agent_tools.bind(HttpBackend(base))

    agent = agent_build.build_agent()
    return to_a2a(
        agent,
        host='0.0.0.0',
        port=agent_config.AGENT_A2A_PORT,
    )


# uvicorn imports this module and looks for `app`
app = _build()
