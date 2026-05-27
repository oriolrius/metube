"""Build the shared `LlmAgent` instance.

Imported by both the embedded aiohttp routes and the A2A sidecar so
they share the same model + tool wiring (D3: same *code*, separate
Python objects per process).
"""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from . import config as agent_config
from . import tools as agent_tools


SYSTEM_PROMPT = """\
You are MeTube's in-app assistant. You help the user manage video
downloads and subscriptions on a single-user, self-hosted MeTube
instance.

Conventions:
- Be terse. Confirm only what the user can't see in the UI.
- For destructive actions (deleting downloads or subscriptions) the
  tool itself will ask the user to confirm before running — you do not
  need to ask again in chat.
- Prefer the `presets` system for yt-dlp options when available; fall
  back to per-call overrides only when necessary.
"""


def build_agent() -> LlmAgent:
    """Construct the agent. Call `tools.bind(...)` before this."""
    model = LiteLlm(
        model=f'openai/{agent_config.AGENT_MODEL}',
        api_base=agent_config.LITELLM_BASE_URL,
        api_key=agent_config.LITELLM_API_KEY,
    )
    return LlmAgent(
        name='metube',
        model=model,
        instruction=SYSTEM_PROMPT,
        tools=agent_tools.build_tools(),
    )
