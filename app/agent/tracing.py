"""OpenTelemetry → Langfuse wiring for the agent.

Called once per process (MeTube and the A2A sidecar). No-op when the
required `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` are not set, so
the agent can run without observability during development.

Idempotent: safe to call twice.
"""

from __future__ import annotations

import base64
import logging
import os

from . import config as agent_config

log = logging.getLogger(__name__)

_installed = False


def setup_tracing(process_role: str) -> bool:
    """Install OTEL tracer + ADK instrumentor. `process_role` is e.g.
    'embedded' or 'a2a' so traces from the two MeTube processes are
    distinguishable in Langfuse. Returns True if installed."""
    global _installed
    if _installed:
        return True
    if not agent_config.LANGFUSE_PUBLIC_KEY or not agent_config.LANGFUSE_SECRET_KEY:
        log.info('Langfuse keys not set; skipping OTEL setup.')
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor
    except ImportError:
        log.warning('OTEL / openinference deps missing; install [agent] extras.')
        return False

    auth = base64.b64encode(
        f'{agent_config.LANGFUSE_PUBLIC_KEY}:{agent_config.LANGFUSE_SECRET_KEY}'.encode()
    ).decode()
    endpoint = agent_config.LANGFUSE_HOST.rstrip('/') + '/api/public/otel/v1/traces'

    resource = Resource.create({
        'service.name': agent_config.OTEL_SERVICE_NAME,
        'process.role': process_role,
        'service.version': os.environ.get('METUBE_VERSION', 'dev'),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
        endpoint=endpoint,
        headers={'Authorization': f'Basic {auth}'},
    )))
    trace.set_tracer_provider(provider)
    GoogleADKInstrumentor().instrument(tracer_provider=provider)
    _installed = True
    log.info('OTEL → Langfuse tracing installed (process_role=%s)', process_role)
    return True
