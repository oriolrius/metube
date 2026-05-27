"""Agent-specific environment configuration.

Read once at import time. Returns plain values; the active feature flag
is `AGENT_ENABLED`, which is also surfaced to the UI through
`Config._FRONTEND_KEYS` in `app/main.py`.
"""

from __future__ import annotations

import os


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


AGENT_ENABLED: bool = _bool('AGENT_ENABLED', False)

LITELLM_BASE_URL: str = os.environ.get(
    'LITELLM_BASE_URL', 'https://litellm.joor.net/v1'
)
LITELLM_API_KEY: str = os.environ.get('LITELLM_API_KEY', '')
AGENT_MODEL: str = os.environ.get('AGENT_MODEL', 'claude-haiku-free')

AGENT_A2A_PORT: int = int(os.environ.get('AGENT_A2A_PORT', '8082'))

LANGFUSE_HOST: str = os.environ.get('LANGFUSE_HOST', 'https://lf.joor.net')
LANGFUSE_PUBLIC_KEY: str = os.environ.get('LANGFUSE_PUBLIC_KEY', '')
LANGFUSE_SECRET_KEY: str = os.environ.get('LANGFUSE_SECRET_KEY', '')
OTEL_SERVICE_NAME: str = os.environ.get('OTEL_SERVICE_NAME', 'metube-agent')

SESSION_ID = 'metube-shared'  # D2: one shared in-memory session
