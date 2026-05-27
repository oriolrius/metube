# MeTube + Agent Plan

Add a conversational agent on top of MeTube. The agent runs **in-process**
alongside the aiohttp server, exposing one tool per existing MeTube
operation by calling MeTube's Python objects directly (no HTTP loopback).
A separate sidecar process publishes the same agent over Google's A2A
protocol for external callers.

## Locked decisions (driving this plan)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Tools call MeTube in-process** via `dqueue` / `submgr` / `Notifier` / `config` — no HTTP loopback. | No auth exists in MeTube (`grep` confirms); loopback would add latency with no honesty benefit. |
| D2 | **One shared in-memory session**, `session_id="metube-shared"`. No per-browser context, no auth. | Single-user homelab assumption. Drops `AGENT_SESSION_DB` from the design. |
| D3 | **Embedded `/agent/chat` SSE** in MeTube **+ A2A sidecar process** sharing the same `LlmAgent` *code* (not the same object). | `to_a2a()` returns a Starlette ASGI app and cannot mount under aiohttp; the sidecar is the only working shape. |
| D4 | **HITL only for `delete_downloads` and `delete_subscriptions`.** All other mutating tools auto-execute. | Limits friction; only truly destructive actions gate behind confirmation. |
| D5 | **Tracing day one**: ADK (OTEL) + LiteLLM both → Langfuse at `https://lf.joor.net`. | Two views — agent reasoning and proxy cost — merged by shared `trace_id`. |
| D6 | **`google-adk>=2.1,<3`** (2.1.0 released 2025-12-12, supports Python 3.13). HITL uses ADK's native `FunctionTool(require_confirmation=…)` API. **`AGENT_MODEL=claude-haiku-free`** (LiteLLM alias routing direct to Anthropic `claude-haiku-4-5`). | Verified via PyPI + adk.dev + LiteLLM `/v1/models`. See "Step 0 results" below. |

## Goals

1. Keep MeTube's UI and HTTP API unchanged for the browser. New routes
   live under `config.URL_PREFIX + 'agent/...'` (every existing route
   does, see `app/main.py:709-1045`).
2. Add an **agent module** under `app/agent/` that builds an ADK
   `LlmAgent` wired to in-process tools and a LiteLLM-backed model.
3. Route all LLM calls through the user's LiteLLM proxy
   (`https://litellm.joor.net/v1`) using a budget-capped virtual key.
4. Ship two runtime shapes from the same agent code:
   - **Embedded**: aiohttp SSE route `/agent/chat` driven by ADK
     `Runner`.
   - **A2A sidecar**: a second Python process running
     `to_a2a(build_agent())` as a Starlette/uvicorn app.
5. Surface the agent in the existing Angular UI as a chat panel, gated
   by an `AGENT_ENABLED` flag added to `Config._FRONTEND_KEYS`
   (`app/main.py:129`).

## Architecture

```
 Browser ──► /  (Angular UI, unchanged)
         ──► /<prefix>add /delete /history /subscriptions/… (existing)
         ──► /<prefix>agent/chat    (NEW — SSE)
         ──► /<prefix>agent/confirm (NEW — HITL approve/deny for D4)

 MeTube process (aiohttp)
   └── app/agent/  in-process tools call dqueue / submgr / config / Notifier

 metube-agent-a2a process (separate, uvicorn)
   └── from app.agent.build import build_agent
       app = to_a2a(build_agent())
       → /.well-known/agent-card.json + /a2a/v1
```

Both processes import the same `build_agent()` factory from
`app/agent/build.py`. The sidecar imports MeTube's internals too (it
needs `dqueue` / `submgr`), so it runs in the same container with a
shared state directory and binds to a different port.

## Step 0 — verification gate (results)

Completed 2026-05-27. Findings drive the rest of the plan.

- **google-adk 2.1.0** released 2025-12-12 (PyPI), `requires_python>=3.10`,
  classifiers list Python 3.13. Pin: `google-adk>=2.1,<3`.
- **HITL is built in**: `FunctionTool(fn, require_confirmation=True|callable)`
  and inside the tool `tool_context.request_confirmation(hint, payload)` /
  `tool_context.tool_confirmation`. No `NodeInterruptedError`, no graph
  engine — earlier drafts of this plan invented those. Approval flow:
  caller resumes the runner by sending a `FunctionResponse` with
  `confirmed=True` and the payload. Source:
  https://adk.dev/tools-custom/confirmation/
- **LiteLlm wrapper**: `from google.adk.models.lite_llm import LiteLlm`.
  Constructor is `LiteLlm(model: str, **kwargs)`. Both `api_base` and
  `api_key` are passed through as kwargs to the underlying `litellm`
  call.
- **A2A helper**: `from google.adk.a2a.utils.agent_to_a2a import to_a2a`.
  Signature: `to_a2a(agent, *, host="localhost", port=8000, protocol="http",
  agent_card=None, ...) -> Starlette`. Marked `@a2a_experimental` in
  2.1.0 — acceptable but worth pinning carefully.
- **OpenInference instrumentor**: `openinference-instrumentation-google-adk`
  0.1.15 on PyPI, import
  `from openinference.instrumentation.google_adk import GoogleADKInstrumentor`.
  Supports Python 3.13.
- **LiteLLM proxy alias**: `claude-haiku-free` routes direct to Anthropic
  `claude-haiku-4-5` (200k input tokens, no OpenRouter hop). Confirmed via
  `GET /model/info`. This is `AGENT_MODEL`.

Open follow-ups (not blocking implementation):

- Confirm `claude-haiku-free`'s rate-limit / "free" semantics on the
  proxy (LiteLLM team budget vs Anthropic free tier — TBD when we issue
  the virtual key).

## Tool surface

All tools live in `app/agent/tools.py` and call MeTube's Python objects
directly (D1). Imports from `app/main.py`:

- `from main import config, dqueue, submgr, sio, serializer`
- `from main import parse_download_options, _validate_subscription_update_payload`
  (or whatever the canonical validators are; check `app/main.py` after
  Step 0).
- `from main import COOKIES_PATH` and the cookie helpers used by the
  `upload-cookies` / `delete-cookies` routes.

For each tool below, the **canonical signature** is whatever
`parse_download_options` (`app/main.py`, called from the `add` and
`subscribe` routes at `:709` and `:761`) accepts — do not redeclare;
generate the FunctionTool signature from that single source.

### Downloads (call `dqueue` — `app/ytdl.py:748`)

| Tool | Backing call | HITL? |
|---|---|---|
| `add_download(...)` | `dqueue.add(...)` (`app/ytdl.py:1044`) | auto |
| `cancel_add()` | `dqueue.cancel_add()` (`app/ytdl.py:761`) | auto |
| `start_downloads(ids)` | mirror `POST start` handler (`app/main.py:868`) | auto |
| `delete_downloads(ids, where)` | `dqueue.delete(...)` (`app/ytdl.py:731`) | **HITL** |
| `get_history()` | mirror `GET history` (`app/main.py:941`) | auto |
| `list_presets()` | `sorted(config.YTDL_OPTIONS_PRESETS.keys())` | auto |
| `get_version()` | mirror `GET version` (`app/main.py:1045`) | auto |

### Subscriptions (call `submgr` — `app/subscriptions.py:302`)

| Tool | Backing call | HITL? |
|---|---|---|
| `subscribe(...)` | `submgr.add_subscription(...)` (`:470`) | auto |
| `list_subscriptions()` | `submgr.list_all()` (`:429`) | auto |
| `update_subscription(id, changes)` | `submgr.update_subscription(...)` (`:611`) | auto |
| `delete_subscriptions(ids)` | `submgr.delete_subscriptions(...)` (`:589`) | **HITL** |
| `check_subscriptions_now(ids=None)` | `submgr.check_now(...)` (`:664`) | auto |

### Cookies

| Tool | Backing call | HITL? |
|---|---|---|
| `upload_cookies(content_b64)` | replicate `POST upload-cookies` (`app/main.py:879`) — decode b64, write to `COOKIES_PATH`, call `config.set_runtime_override('cookiefile', COOKIES_PATH)` | auto |
| `delete_cookies()` | replicate `POST delete-cookies` (`app/main.py:906`) | auto |
| `cookie_status()` | replicate `GET cookie-status` (`app/main.py:933`) | auto |

The 1 MB cap enforced by the HTTP endpoint must be replicated here.

### Live state (Socket.IO events)

The existing `Notifier` class (`app/main.py:446`) is the in-process
event hub: methods `added` / `updated` / `completed` / `canceled` /
`cleared` are called by `dqueue` and currently fan out via `sio.emit`.

Approach: **subclass or wrap `Notifier`** so the agent module subscribes
to the same callbacks and maintains a small in-memory snapshot. Expose
`get_live_state()` returning that snapshot. **Do not** open a
Socket.IO client connection to ourselves.

## File layout

```
app/agent/
  __init__.py
  build.py         # build_agent() factory — shared by embedded + A2A sidecar
  tools.py         # FunctionTool wrappers (D1, in-process)
  live_state.py    # Notifier subclass + snapshot for get_live_state()
  hitl.py          # confirmation token store + approve/deny helpers (D4)
  sse.py           # aiohttp SSE handler for /agent/chat
  routes.py        # register_agent_routes(app, routes)
  config.py        # env vars (table below)
a2a_sidecar.py     # `from app.agent.build import build_agent; app = to_a2a(build_agent())`
```

Mount in `app/main.py` immediately before the existing
`app.add_routes(routes)` call (`app/main.py:1065`):

```python
if config.AGENT_ENABLED:
    from agent.routes import register_agent_routes
    register_agent_routes(routes, config, dqueue, submgr)
app.add_routes(routes)
```

Pass dependencies in explicitly — avoid the import-time coupling that
makes testing painful.

## Configuration

New env vars (read in `app/agent/config.py`, surfaced in `Dockerfile`
and `docker-entrypoint.sh`):

| Var | Default | Purpose |
|---|---|---|
| `AGENT_ENABLED` | `false` | Feature flag — also added to `Config._FRONTEND_KEYS` (`app/main.py:129`) so the UI knows whether to render the chat panel |
| `LITELLM_BASE_URL` | `https://litellm.joor.net/v1` | OpenAI-compatible endpoint |
| `LITELLM_API_KEY` | (required when enabled) | Budget-capped virtual key `sk-litellm-…` |
| `AGENT_MODEL` | `claude-haiku-free` | LiteLLM proxy alias → Anthropic `claude-haiku-4-5` direct (verified in Step 0) |
| `AGENT_A2A_PORT` | `8082` | Sidecar uvicorn port (only used by `a2a_sidecar.py`) |
| `LANGFUSE_HOST` | `https://lf.joor.net` | OTEL exporter target |
| `LANGFUSE_PUBLIC_KEY` | (required when enabled) | `pk-lf-…` |
| `LANGFUSE_SECRET_KEY` | (required when enabled) | `sk-lf-…` |
| `OTEL_SERVICE_NAME` | `metube-agent` | Service label in Langfuse |

No `AGENT_SESSION_DB` (D2). No `METUBE_INTERNAL_URL` (D1).

## LiteLLM integration

1. **Issue a virtual key** scoped to the chosen model + a small budget:
   ```bash
   bash ~/.claude/skills/skill-litellm/scripts/api.sh \
     POST /key/generate -d '{
       "key_alias": "metube-agent",
       "models": ["<AGENT_MODEL from Step 0>"],
       "max_budget": 5,
       "metadata": {"app":"metube"}
     }'
   ```
   Store the returned `sk-litellm-…` in Bitwarden as
   `MeTube Agent LiteLLM Key` and inject via `LITELLM_API_KEY`.

2. **ADK ↔ LiteLLM wiring** (`app/agent/build.py`), verified shape:
   ```python
   from google.adk.agents import LlmAgent
   from google.adk.models.lite_llm import LiteLlm

   def build_agent():
       model = LiteLlm(
           model=f"openai/{AGENT_MODEL}",  # forces LiteLLM's OAI-compat router
           api_base=LITELLM_BASE_URL,
           api_key=LITELLM_API_KEY,
       )
       return LlmAgent(
           name="metube",
           model=model,
           instruction=SYSTEM_PROMPT,
           tools=[...],  # from app.agent.tools
       )
   ```
   `LiteLlm.__init__` is `(model, **kwargs)`; everything except `model`
   is forwarded to the underlying `litellm.completion` call, so any
   LiteLLM-supported kwarg works.

3. **Tagging**: pass
   `extra_body={"metadata": {"project": "metube", "tags": ["metube-agent"]}}`
   on each request so Langfuse filters work.

## HITL (D4 — only delete_downloads and delete_subscriptions)

Two tools require confirmation: `delete_downloads`, `delete_subscriptions`.

Mechanism (verified in Step 0): ADK's built-in confirmation API.

```python
from google.adk.tools import FunctionTool, ToolContext

async def delete_downloads(ids: list[str], where: str,
                            tool_context: ToolContext) -> dict:
    if not tool_context.tool_confirmation:
        tool_context.request_confirmation(
            hint=f"Delete {len(ids)} item(s) from '{where}'?",
            payload={"ids": ids, "where": where},
        )
        return {"status": "awaiting_confirmation"}
    # On resume, payload comes back through tool_confirmation.payload
    payload = tool_context.tool_confirmation.payload
    return await _do_delete(payload["ids"], payload["where"])

tools = [
    FunctionTool(delete_downloads, require_confirmation=True),
    # …
]
```

Wiring on the aiohttp side (`app/agent/sse.py` + `routes.py`):

- The SSE handler relays the `request_confirmation` event verbatim to
  the UI (it includes the `hint`, `payload`, and a confirmation id the
  runner generated).
- The UI POSTs to `<prefix>agent/confirm` with
  `{confirmation_id, approve: bool, payload_overrides?: dict}`.
- The handler resumes the runner by sending a `FunctionResponse` with
  the confirmed/denied status — exact API on `Runner` is verified at
  implementation time against installed 2.1.x.

`app/agent/hitl.py` holds the **pending-confirmation map** (id → SSE
session) so the resume can find the right runner stream. In-memory
only (D2).

D2 caveat (honest about the tradeoff): with a single shared session,
**any browser viewing the chat can approve any other browser's pending
deletion**. Acceptable under the single-user homelab assumption that
drove D2. If that assumption changes, revisit D2 and D4 together.

## Embedded + A2A sidecar (D3)

**Embedded** — `app/agent/routes.py` registers two aiohttp routes onto
the existing `routes` table, prefixed with `config.URL_PREFIX`:

- `POST <prefix>agent/chat` — SSE stream of ADK runner events.
- `POST <prefix>agent/confirm` — HITL approve/deny.

**A2A sidecar** — `a2a_sidecar.py` at repo root:

```python
from google.adk.a2a.utils.agent_to_a2a import to_a2a
from app.agent.build import build_agent

# to_a2a is decorated @a2a_experimental in 2.1.x; signature:
#   to_a2a(agent, *, host="localhost", port=8000, protocol="http",
#          agent_card=None, ...) -> starlette.applications.Starlette
app = to_a2a(build_agent(), host="0.0.0.0", port=int(os.environ.get("AGENT_A2A_PORT", "8082")))
# run with: uvicorn a2a_sidecar:app --host 0.0.0.0 --port ${AGENT_A2A_PORT:-8082}
```

Sidecar publishes `/.well-known/agent-card.json` and `/a2a/v1` on its
own port. Both processes share the host filesystem (and therefore
`STATE_DIR`, `COOKIES_PATH`, downloaded files). They do **not** share
the `LlmAgent` Python object — each process builds its own (D3).

Consequence to document for callers: the two processes have **separate
in-memory state** (separate ADK sessions, separate HITL token stores,
separate `live_state` snapshots). The UI talks to the embedded one;
external A2A clients talk to the sidecar.

## Frontend

Add a collapsible chat panel to the Angular UI:

- New component `ui/src/app/agent/agent-chat.component.ts`.
- Service opens `EventSource('<prefix>agent/chat?session=metube-shared')`
  and POSTs messages to the same URL.
- Toggle button in the navbar; hidden when `AGENT_ENABLED` is `false`.
  Plumbing: add `'AGENT_ENABLED'` to `Config._FRONTEND_KEYS`
  (`app/main.py:129`); the existing `frontend_safe()` (`:140`) already
  exposes it to the UI.

## Security

- No HTTP auth on MeTube today (confirmed by grep). `/agent/*` routes
  inherit that — i.e. **the agent is unauthenticated**, same as the
  rest of MeTube. Document this explicitly in the README rather than
  pretending an auth model that doesn't exist.
- LiteLLM virtual key has a hard `max_budget`; rotate quarterly.
- Cookie-upload tool enforces the same 1 MB cap as the HTTP endpoint.
- A2A sidecar binds to `127.0.0.1` only by default.

## Observability — Langfuse (D5)

Both trace sources from day one.

1. **ADK side** — instrument the Python process with OpenTelemetry +
   OpenInference's ADK instrumentor (verify exact package name in
   Step 0). Sketch:
   ```python
   from opentelemetry import trace
   from opentelemetry.sdk.trace import TracerProvider
   from opentelemetry.sdk.trace.export import BatchSpanProcessor
   from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
   from openinference.instrumentation.google_adk import GoogleADKInstrumentor

   provider = TracerProvider()
   provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
       endpoint=f"{LANGFUSE_HOST}/api/public/otel/v1/traces",
       headers={"Authorization": f"Basic {LANGFUSE_BASIC_AUTH}"},  # base64(pk:sk)
   )))
   trace.set_tracer_provider(provider)
   GoogleADKInstrumentor().instrument()
   ```
   Run this in both the MeTube process and the A2A sidecar; use
   `OTEL_SERVICE_NAME=metube-agent` and add a `process` resource
   attribute (`embedded` vs `a2a`) so Langfuse distinguishes them.

2. **LiteLLM proxy side** — already supports Langfuse callbacks. Set in
   `/opt/stacks/litellm/config.yaml`:
   ```yaml
   litellm_settings:
     success_callback: ["langfuse"]
     failure_callback: ["langfuse"]
   ```
   with `LANGFUSE_*` env vars in the LiteLLM stack `.env`.

Trace IDs: ADK creates the root span; LiteLLM picks up the OTEL
context from the outbound HTTP headers so spans share a `trace_id`.
Confirm propagation works during smoke test.

Store `pk-lf-…` / `sk-lf-…` in Bitwarden as `MeTube Langfuse keys`.

## Step-by-step delivery

0. **Verification gate** (see §Step 0). Update affected sections of
   this plan before continuing.
1. **Scaffold `app/agent/`** with empty modules; add `google-adk`,
   `litellm`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`,
   and the OpenInference ADK instrumentor to `pyproject.toml`;
   `uv sync`.
2. **Wire `get_version` end-to-end** as the smoke test: build the
   `LlmAgent`, expose it behind `/<prefix>agent/chat` SSE, ask "what
   version is running?", confirm the tool fires.
3. **Implement remaining tools** in the order: history → add → delete
   (with HITL) → start / cancel_add → presets → cookies →
   subscriptions (with HITL on delete_subscriptions only). Wire
   `live_state.py` last by subclassing `Notifier` (`app/main.py:446`).
4. **HITL plumbing**: token store, `/<prefix>agent/confirm` route,
   SSE relaying of `confirmation_required` events. Mechanism per
   Step 0 outcome.
5. **Issue LiteLLM key**, set env, smoke-test with the model chosen in
   Step 0.
6. **Wire Langfuse** in both processes; verify a single trace shows
   both ADK and LiteLLM spans for one `/agent/chat` request.
7. **UI panel** + `AGENT_ENABLED` flag added to `Config._FRONTEND_KEYS`.
8. **A2A sidecar**: `a2a_sidecar.py`, `uvicorn` invocation, a second
   process entry in `docker-entrypoint.sh` (or a separate compose
   service if simpler).
9. **Tests**:
   - `app/tests/test_agent_tools.py`: each tool against an in-memory
     `dqueue` / `submgr` (reuse fixtures from `test_download_queue.py`
     and `test_subscriptions.py`).
   - `app/tests/test_agent_hitl.py`: deletion is gated; approve and
     deny paths.
   - `app/tests/test_agent_e2e.py`: spins up the app, sends a natural-
     language prompt, asserts the right tool is called
     (record/replay LiteLLM via `responses` or pytest-recording).
10. **Dockerfile**: install ADK + OTEL deps; add the A2A sidecar to the
    entrypoint (background process) honoring `AGENT_*` envs.

## Things explicitly *not* in this plan

- **No Claude Code marketplace skill** for the running agent. A skill
  is markdown + scripts loaded into the CLI; a long-running HTTP/A2A
  service isn't that. If we want CLI access later, ship a thin skill
  that calls the deployed `/agent/chat` or A2A endpoint — separate
  effort.
- **No MCP server**. A2A already exposes the agent (model + system
  prompt + tools); MCP would expose only the tools and lose the
  reasoning. Revisit if a non-agent client needs raw tools.
- **No DatabaseSessionService, no per-user sessions, no auth.**
  Locked out by D2.
