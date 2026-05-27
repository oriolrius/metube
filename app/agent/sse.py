"""aiohttp SSE handler that wraps the ADK `Runner`, plus HITL confirm route.

Endpoints:
 - POST <prefix>agent/chat    — accepts {"message": "..."}, streams events
 - POST <prefix>agent/confirm — accepts
       {"function_call_id": "...", "name": "...",
        "confirmed": bool, "payload": {...}}
   and resumes the runner by sending a FunctionResponse keyed to the
   original tool call id.

Sessions: single shared `metube-shared` session (D2). Concurrent
requests against the same session serialise on `_session_lock` to
avoid interleaved tool calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

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
        self._session_lock = asyncio.Lock()
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

    async def stream(self, response: web.StreamResponse,
                     new_message: genai_types.Content) -> None:
        """Drive the runner with `new_message` and stream events to `response`."""
        await self.ensure_session()
        async with self._session_lock:
            try:
                async for event in self.runner.run_async(
                    user_id=USER_ID,
                    session_id=agent_config.SESSION_ID,
                    new_message=new_message,
                ):
                    payload = _event_to_dict(event)
                    await response.write(
                        f'data: {json.dumps(payload)}\n\n'.encode('utf-8')
                    )
            except Exception:
                log.exception('agent runner error')
                await response.write(b'event: error\ndata: {}\n\n')


async def chat_handler(request: web.Request) -> web.StreamResponse:
    runtime: AgentRuntime = request.app['agent_runtime']

    body = await request.json()
    message = (body.get('message') or '').strip()
    if not message:
        raise web.HTTPBadRequest(reason='message is required')

    response = _sse_response()
    await response.prepare(request)

    user_content = genai_types.Content(
        role='user',
        parts=[genai_types.Part(text=message)],
    )
    await runtime.stream(response, user_content)
    await response.write_eof()
    return response


async def confirm_handler(request: web.Request) -> web.StreamResponse:
    """Resume the runner after a HITL confirmation."""
    runtime: AgentRuntime = request.app['agent_runtime']

    body = await request.json()
    fc_id = body.get('function_call_id')
    name = body.get('name')
    confirmed = bool(body.get('confirmed', False))
    payload = body.get('payload')

    if not fc_id or not name:
        raise web.HTTPBadRequest(reason='function_call_id and name are required')

    # Per ADK convention, resume by sending a FunctionResponse whose
    # `response` is the serialised ToolConfirmation. The framework
    # wires it into `tool_context.tool_confirmation` on re-invocation.
    fr = genai_types.FunctionResponse(
        id=fc_id,
        name=name,
        response={'confirmed': confirmed, 'payload': payload},
    )
    resume_content = genai_types.Content(
        role='user',
        parts=[genai_types.Part(function_response=fr)],
    )

    response = _sse_response()
    await response.prepare(request)
    await runtime.stream(response, resume_content)
    await response.write_eof()
    return response


def _sse_response() -> web.StreamResponse:
    return web.StreamResponse(
        status=200,
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


def _event_to_dict(event: Any) -> dict:
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
                {'id': fc.id, 'name': fc.name, 'args': dict(fc.args or {})}
                for fc in fcs
            ]
        frs = [p.function_response for p in content.parts if getattr(p, 'function_response', None)]
        if frs:
            out['function_responses'] = [
                {'id': fr.id, 'name': fr.name, 'response': dict(fr.response or {})}
                for fr in frs
            ]
    # Surface the request_input action (HITL trigger) if present.
    actions = getattr(event, 'actions', None)
    req_input = getattr(actions, 'request_input', None) if actions else None
    if req_input is not None:
        out['request_input'] = {
            'interrupt_id': getattr(req_input, 'interrupt_id', None),
            'message': getattr(req_input, 'message', None),
            'payload': getattr(req_input, 'payload', None),
        }
    return out
