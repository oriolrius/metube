"""aiohttp SSE handler that wraps the ADK `Runner`.

Built out in Step 2. The handler:
 - Reads the user message from the request body
 - Drives `Runner.run_async(...)` against the shared `metube-shared` session
 - Streams events to the browser as `text/event-stream`
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import web
from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from . import config as agent_config

log = logging.getLogger(__name__)

APP_NAME = 'metube'
USER_ID = 'metube'


class AgentRuntime:
    """One-per-process runtime: session service + Runner wrapping the agent."""

    def __init__(self, agent: LlmAgent) -> None:
        self.session_service = InMemorySessionService()
        self.runner = Runner(
            app_name=APP_NAME,
            agent=agent,
            session_service=self.session_service,
        )
        self._init_lock = asyncio.Lock()
        self._session_ready = False

    async def ensure_session(self) -> None:
        if self._session_ready:
            return
        async with self._init_lock:
            if self._session_ready:
                return
            await self.session_service.create_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=agent_config.SESSION_ID,
            )
            self._session_ready = True


async def chat_handler(request: web.Request) -> web.StreamResponse:
    runtime: AgentRuntime = request.app['agent_runtime']
    await runtime.ensure_session()

    body = await request.json()
    message = body.get('message', '').strip()
    if not message:
        raise web.HTTPBadRequest(reason='message is required')

    response = web.StreamResponse(
        status=200,
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )
    await response.prepare(request)

    user_content = genai_types.Content(
        role='user',
        parts=[genai_types.Part(text=message)],
    )

    try:
        async for event in runtime.runner.run_async(
            user_id=USER_ID,
            session_id=agent_config.SESSION_ID,
            new_message=user_content,
        ):
            payload = _event_to_dict(event)
            await response.write(
                f'data: {json.dumps(payload)}\n\n'.encode('utf-8')
            )
    except Exception:
        log.exception('agent runner error')
        await response.write(b'event: error\ndata: {}\n\n')
    finally:
        await response.write_eof()
    return response


def _event_to_dict(event) -> dict:
    """Compact dict representation of an ADK event for the UI."""
    out: dict = {
        'author': getattr(event, 'author', None),
        'is_final': bool(getattr(event, 'is_final_response', lambda: False)()),
    }
    content = getattr(event, 'content', None)
    if content and getattr(content, 'parts', None):
        text_parts = [p.text for p in content.parts if getattr(p, 'text', None)]
        if text_parts:
            out['text'] = ''.join(text_parts)
        fcs = [p.function_call for p in content.parts if getattr(p, 'function_call', None)]
        if fcs:
            out['function_calls'] = [
                {'name': fc.name, 'args': dict(fc.args or {})} for fc in fcs
            ]
        frs = [p.function_response for p in content.parts if getattr(p, 'function_response', None)]
        if frs:
            out['function_responses'] = [
                {'name': fr.name, 'response': dict(fr.response or {})} for fr in frs
            ]
    return out
