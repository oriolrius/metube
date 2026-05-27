"""Live smoke test: drives the real LlmAgent against the real LiteLLM proxy.

Not part of `pytest` (real network + paid tokens). Run manually:

    cd app && AGENT_ENABLED=true LITELLM_API_KEY=... uv run python tests/smoke_agent_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# Make app/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import types as genai_types

from agent import build as agent_build
from agent import config as agent_config
from agent import tools as agent_tools
from agent.sse import APP_NAME, USER_ID, AgentRuntime


def _fake_dqueue():
    class _D:
        queue = SimpleNamespace(saved_items=lambda: [])
        done = SimpleNamespace(saved_items=lambda: [])
        pending = SimpleNamespace(saved_items=lambda: [])
    return _D()


def _fake_submgr():
    class _S:
        def list_all(self): return []
    return _S()


class _CfgShim:
    YTDL_OPTIONS = {}
    YTDL_OPTIONS_PRESETS = {'audio': {}, 'video': {}}
    def frontend_safe(self): return {'AGENT_ENABLED': True}
    def set_runtime_override(self, k, v): pass
    def remove_runtime_override(self, k): pass
    def load_ytdl_options(self): return True, ''


async def main() -> int:
    if not agent_config.LITELLM_API_KEY:
        print('LITELLM_API_KEY is empty; aborting.')
        return 2

    agent_tools.bind(
        dqueue=_fake_dqueue(), submgr=_fake_submgr(), config=_CfgShim(),
        cookies_path='/tmp/cookies.txt', live_state=None,
    )
    runtime = AgentRuntime(agent_build.build_agent())
    await runtime.ensure_session()

    user = genai_types.Content(
        role='user',
        parts=[genai_types.Part(text='What MeTube version is running? Use the get_version tool.')],
    )

    saw_get_version = False
    final_text_parts: list[str] = []
    async for event in runtime.runner.run_async(
        user_id=USER_ID, session_id=agent_config.SESSION_ID, new_message=user,
    ):
        content = getattr(event, 'content', None)
        if content and getattr(content, 'parts', None):
            for p in content.parts:
                fc = getattr(p, 'function_call', None)
                if fc is not None:
                    print(f'TOOL CALL: {fc.name}({dict(fc.args or {})})')
                    if fc.name == 'get_version':
                        saw_get_version = True
                fr = getattr(p, 'function_response', None)
                if fr is not None:
                    print(f'TOOL RESPONSE: {fr.name} → {dict(fr.response or {})}')
                txt = getattr(p, 'text', None)
                if txt:
                    final_text_parts.append(txt)

    final_text = ''.join(final_text_parts)
    print('---')
    print('FINAL:', final_text.strip())
    print('saw_get_version:', saw_get_version)
    return 0 if saw_get_version and final_text.strip() else 1


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
