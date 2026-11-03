"""OpenTelemetry for the worker: traces, metrics and logs, exported over OTLP/HTTP.

Everything is switched on by OTEL_EXPORTER_OTLP_ENDPOINT (for example
http://otel-collector:4318, set in docker-compose.yml). Without it nothing is exported and the
API works exactly as before: the OpenTelemetry API hands out no-op tracers and meters, so the
tests and a plain `uvicorn main:app` need no collector.

What the worker reports
  traces   one span per HTTP request (FastAPI instrumentation), with child spans for the
           compute-slot wait and every pipeline stage (DEM window, depression filling, flow
           accumulation, catchment, rainfall, map layers), outbound downloads and SQL queries
  metrics  HTTP server metrics, analysis and stage durations, compute-slot waits and
           rejections, cache results, catchment/water outcomes, process CPU and memory
  logs     Python logging records, each carrying the trace and span id of the request
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager

from opentelemetry import metrics, trace

tracer = trace.get_tracer("jaldrishti.worker")
meter = metrics.get_meter("jaldrishti.worker")
log = logging.getLogger("jaldrishti.worker")

_SECONDS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120]

# Instruments are created at import time; the API binds them to the real meter provider as
# soon as setup() installs it (and they stay no-ops if it never does).
ANALYSIS_S = meter.create_histogram(
    "pond.analysis.duration", unit="s", explicit_bucket_boundaries_advisory=_SECONDS,
    description="Time to analyse one land parcel or contour map inside the worker")
STAGE_S = meter.create_histogram(
    "pond.analysis.stage.duration", unit="s", explicit_bucket_boundaries_advisory=[0.001, 0.0025] + _SECONDS,
    description="Time spent in one stage of the analysis pipeline")
WAIT_S = meter.create_histogram(
    "pond.compute.wait.duration", unit="s",
    explicit_bucket_boundaries_advisory=[0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20],
    description="Time a request waited for the worker's single compute slot")
INFLIGHT = meter.create_up_down_counter(
    "pond.compute.inflight", unit="{request}",
    description="Analyses running or waiting for the compute slot on this worker")
REJECTED = meter.create_counter(
    "pond.compute.rejected", unit="{request}",
    description="Requests answered 503 because the compute slot stayed busy")
RAIN_LOOKUPS = meter.create_counter(
    "pond.rainfall.lookups", unit="{lookup}",
    description="Rainfall series lookups by where the data came from")
FLOW_CACHE = meter.create_counter(
    "pond.flow_model.cache", unit="{lookup}",
    description="Contour-map flow models served from the cache (hit) or rebuilt (miss)")
CATCHMENT_HA = meter.create_histogram(
    "pond.catchment.area", unit="ha", explicit_bucket_boundaries_advisory=[1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000],
    description="Catchment area of the suggested pond sites")
COLLECTABLE_M3 = meter.create_histogram(
    "pond.water.collectable", unit="m3",
    explicit_bucket_boundaries_advisory=[100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000],
    description="Water the suggested pond can collect in an average year")

_providers: list = []


def enabled() -> bool:
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")) and os.environ.get("OTEL_SDK_DISABLED", "").lower() != "true"


class _ConsoleFormat(logging.Formatter):
    """`docker logs` lines with the trace id, so a line can be looked up in Jaeger."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = trace.get_current_span().get_span_context()
        trace_id = format(ctx.trace_id, "032x") if ctx.is_valid else "-"
        line = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name} trace={trace_id} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup(app, service_version: str, instance: str) -> None:
    """Install the SDK providers and the automatic instrumentation (once, at import of main)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(h.formatter, _ConsoleFormat) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(_ConsoleFormat())
        root.addHandler(console)
    if not enabled() or _providers:
        return

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.logging.handler import LoggingHandler
    from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    # OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES from the environment are merged in too
    resource = Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "pond-worker"),
        "service.namespace": "jaldrishti",
        "service.version": service_version,
        "service.instance.id": instance,
    })

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    # OTEL_METRIC_EXPORT_INTERVAL (ms) sets how often metrics are pushed; compose uses 10 s
    meter_provider = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    otel_handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    root.addHandler(otel_handler)
    logging.getLogger("uvicorn").addHandler(otel_handler)  # uvicorn's loggers do not propagate to root

    # the gateway asks /api/health every 2 s; tracing it would bury the real requests
    FastAPIInstrumentor.instrument_app(app, excluded_urls="api/health", exclude_spans=["receive", "send"],
                                       tracer_provider=tracer_provider, meter_provider=meter_provider)
    Psycopg2Instrumentor().instrument(tracer_provider=tracer_provider, skip_dep_check=True)
    SystemMetricsInstrumentor(config={
        "process.cpu.time": ["user", "system"],
        "process.cpu.utilization": ["user", "system"],
        "process.memory.usage": None,
        "process.memory.virtual": None,
        "process.thread.count": None,
        "process.open_file_descriptor.count": None,
        "cpython.gc.collections": None,
    }).instrument(meter_provider=meter_provider)

    _providers.extend([tracer_provider, meter_provider, logger_provider])


def shutdown() -> None:
    """Flush what is still buffered (called when uvicorn stops)."""
    while _providers:
        try:
            _providers.pop().shutdown()
        except Exception:  # noqa: BLE001 - never block a shutdown on telemetry
            pass


@contextmanager
def stage(name: str, key: str, ms: dict | None = None, accumulate: bool = False, **attrs):
    """One pipeline stage: a child span, a sample of pond.analysis.stage.duration, and (with
    `ms`) the millisecond value the API already returns in `timings_ms`."""
    t = time.perf_counter()
    with tracer.start_as_current_span(name, attributes={"pond.stage": key, **attrs}) as span:
        try:
            yield span
        finally:
            dt = time.perf_counter() - t
            STAGE_S.record(dt, {"pond.stage": key})
            if ms is not None:
                ms[key] = (ms.get(key, 0.0) if accumulate else 0.0) + dt * 1e3
