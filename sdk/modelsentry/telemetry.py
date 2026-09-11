"""Optional, vendor-neutral OpenTelemetry instrumentation.

ModelSentry is a library, so importing it must not require OpenTelemetry or
mutate an application's global providers.  The helpers in this module use the
active providers when they are present and otherwise degrade to no-ops.  The
optional :func:`configure_telemetry` convenience function is intended for
standalone processes (and only installs providers when the application still
has the SDK's default proxy provider).

Telemetry deliberately contains aggregate operational data only.  In
particular, this module never records exception messages, filesystem paths,
feature names/values, predictions, profile payloads, request bodies, or email
addresses.
"""

from __future__ import annotations

import functools
import inspect
import os
import re
import sys
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, TypeVar

try:  # OpenTelemetry is an optional dependency.
    from opentelemetry import context as _otel_context
    from opentelemetry import metrics as _metrics
    from opentelemetry import trace as _trace
    from opentelemetry.trace import Link as _Link
    from opentelemetry.trace import SpanKind as _SpanKind
    from opentelemetry.trace import Status as _Status
    from opentelemetry.trace import StatusCode as _StatusCode

    _OTEL_API_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised in minimal installs
    _otel_context = None  # type: ignore[assignment]
    _metrics = None  # type: ignore[assignment]
    _trace = None  # type: ignore[assignment]
    _Link = None  # type: ignore[assignment]
    _SpanKind = None  # type: ignore[assignment]
    _Status = None  # type: ignore[assignment]
    _StatusCode = None  # type: ignore[assignment]
    _OTEL_API_AVAILABLE = False

F = TypeVar("F", bound=Callable[..., Any])
_SCOPE = "modelsentry"
_MAX_LINKS = 16
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_INSTRUMENT_CACHE: dict[tuple[str, str], Any] = {}
_INSTRUMENT_CACHE_METER: Any = None


def _enabled() -> bool:
    """Return whether instrumentation has been explicitly disabled."""
    return os.getenv("MODELSENTRY_OTEL_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def include_model_id(explicit: bool = False) -> bool:
    """Whether spans may include a model id (never used for metrics)."""
    return (
        explicit
        or os.getenv("MODELSENTRY_OTEL_INCLUDE_MODEL_ID", "").lower() in _TRUE_VALUES
    )


def _attrs(attributes: Mapping[str, Any] | None) -> dict[str, str | int | float | bool]:
    """Filter attributes to primitive, bounded values accepted by OTel."""
    if not attributes:
        return {}
    result: dict[str, str | int | float | bool] = {}
    for key, value in attributes.items():
        if value is None or not isinstance(value, (str, int, float, bool)):
            continue
        if isinstance(value, str):
            value = value[:128]
        result[str(key)[:64]] = value
    return result


def model_attributes(
    model_id: str | None = None, *, explicit: bool = False
) -> dict[str, Any]:
    """Return an opt-in model attribute for spans; always empty by default."""
    if model_id and include_model_id(explicit):
        return {"modelsentry.model_id": _SAFE_NAME.sub("_", model_id)[:128]}
    return {}


def tracer() -> Any:
    if not _OTEL_API_AVAILABLE or not _enabled():
        return None
    try:
        return _trace.get_tracer(_SCOPE)
    except Exception:
        return None


def meter() -> Any:
    if not _OTEL_API_AVAILABLE or not _enabled():
        return None
    try:
        return _metrics.get_meter(_SCOPE)
    except Exception:
        return None


@contextmanager
def span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    *,
    links: list[Any] | None = None,
) -> Iterator[Any]:
    """Start a safe span from the active provider, or yield a no-op span."""
    tr = tracer()
    if tr is None:
        yield _NoopSpan()
        return
    cm = None
    try:
        cm = tr.start_as_current_span(
            name,
            kind=_SpanKind.INTERNAL if _SpanKind is not None else None,
            attributes=_attrs(attributes),
            links=(links or None),
            # Exception messages and stack traces can contain customer data.
            # Callers record only a sanitized exception type and fixed stage.
            record_exception=False,
            set_status_on_exception=False,
        )
        current = cm.__enter__()
    except Exception:
        # Telemetry must never change ModelSentry business behavior.
        yield _NoopSpan()
        return
    try:
        yield current
    except BaseException:
        # Preserve the business exception even if a provider/exporter fails.
        try:
            cm.__exit__(*sys.exc_info())
        except Exception:
            pass
        raise
    else:
        try:
            status = getattr(getattr(current, "status", None), "status_code", None)
            if (
                _Status is not None
                and _StatusCode is not None
                and status == _StatusCode.UNSET
            ):
                current.set_status(_Status(_StatusCode.OK))
            cm.__exit__(None, None, None)
        except Exception:
            pass


class _NoopSpan:
    def set_attribute(self, _key: str, _value: Any) -> None:
        return None

    def set_status(self, _status: Any) -> None:
        return None

    def add_event(
        self, _name: str, attributes: Mapping[str, Any] | None = None
    ) -> None:
        return None


def set_attributes(target: Any, attributes: Mapping[str, Any]) -> None:
    """Set sanitized attributes on a span without allowing telemetry failures out."""
    try:
        for key, value in _attrs(attributes).items():
            target.set_attribute(key, value)
    except Exception:
        return None


def record_error(target: Any, exc: BaseException, *, stage: str) -> None:
    """Mark a span as failed without recording the exception or its message."""
    error_type = _SAFE_NAME.sub("_", type(exc).__name__)[:64]
    set_attributes(target, {"error.type": error_type, "modelsentry.error.stage": stage})
    try:
        if _Status is not None and _StatusCode is not None:
            target.set_status(_Status(_StatusCode.ERROR))
    except Exception:
        return None


def current_context() -> Any:
    """Capture the current OTel context for safe worker hand-off."""
    if not _OTEL_API_AVAILABLE or not _enabled():
        return None
    try:
        return _otel_context.get_current()
    except Exception:
        return None


def span_links(contexts: list[Any]) -> list[Any]:
    """Build at most 16 links for an aggregate profile batch."""
    if not _OTEL_API_AVAILABLE or _Link is None or _trace is None:
        return []
    links: list[Any] = []
    seen: set[tuple[int, int]] = set()
    for ctx in contexts:
        if ctx is None or len(links) >= _MAX_LINKS:
            break
        try:
            sc = _trace.get_current_span(ctx).get_span_context()
            if not sc.is_valid or (sc.trace_id, sc.span_id) in seen:
                continue
            seen.add((sc.trace_id, sc.span_id))
            links.append(_Link(sc))
        except Exception:
            continue
    return links


def _counter(name: str, description: str) -> Any:
    global _INSTRUMENT_CACHE_METER
    try:
        m = meter()
        if m is None:
            return None
        if m is not _INSTRUMENT_CACHE_METER:
            _INSTRUMENT_CACHE.clear()
            _INSTRUMENT_CACHE_METER = m
        key = ("counter", name)
        if key not in _INSTRUMENT_CACHE:
            _INSTRUMENT_CACHE[key] = m.create_counter(name, description=description)
        return _INSTRUMENT_CACHE[key]
    except Exception:
        return None


def _histogram(name: str, description: str, unit: str = "ms") -> Any:
    global _INSTRUMENT_CACHE_METER
    try:
        m = meter()
        if m is None:
            return None
        if m is not _INSTRUMENT_CACHE_METER:
            _INSTRUMENT_CACHE.clear()
            _INSTRUMENT_CACHE_METER = m
        key = ("histogram", name)
        if key not in _INSTRUMENT_CACHE:
            _INSTRUMENT_CACHE[key] = m.create_histogram(
                name, unit=unit, description=description
            )
        return _INSTRUMENT_CACHE[key]
    except Exception:
        return None


def record_counter(
    name: str, value: int = 1, *, attributes: Mapping[str, Any] | None = None
) -> None:
    instrument = _counter(name, name)
    if instrument is not None:
        try:
            instrument.add(value, _attrs(attributes))
        except Exception:
            return None


def record_duration(
    name: str, duration_ms: float, *, attributes: Mapping[str, Any] | None = None
) -> None:
    instrument = _histogram(name, name)
    if instrument is not None:
        try:
            instrument.record(max(0.0, float(duration_ms)), _attrs(attributes))
        except Exception:
            return None


def record_value(
    name: str, value: float, *, attributes: Mapping[str, Any] | None = None
) -> None:
    """Record a bounded numeric value using a unitless histogram."""
    instrument = _histogram(name, name, unit="1")
    if instrument is not None:
        try:
            instrument.record(max(0.0, float(value)), _attrs(attributes))
        except Exception:
            return None


def instrument_storage(operation: str, object_type: str) -> Callable[[F], F]:
    """Decorate a storage operation with bounded span/metric data."""

    def decorator(fn: F) -> F:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.perf_counter()
                with span(
                    "modelsentry.storage",
                    {
                        "modelsentry.operation": operation,
                        "modelsentry.object": object_type,
                    },
                ) as current:
                    try:
                        result = await fn(*args, **kwargs)
                        set_attributes(current, {"modelsentry.outcome": "success"})
                        return result
                    except Exception as exc:
                        record_error(current, exc, stage="storage")
                        record_counter(
                            "modelsentry.storage.errors",
                            attributes={"operation": operation, "object": object_type},
                        )
                        raise
                    finally:
                        record_duration(
                            "modelsentry.storage.duration",
                            (time.perf_counter() - started) * 1000,
                            attributes={"operation": operation, "object": object_type},
                        )

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            with span(
                "modelsentry.storage",
                {"modelsentry.operation": operation, "modelsentry.object": object_type},
            ) as current:
                try:
                    result = fn(*args, **kwargs)
                    set_attributes(current, {"modelsentry.outcome": "success"})
                    return result
                except Exception as exc:
                    record_error(current, exc, stage="storage")
                    record_counter(
                        "modelsentry.storage.errors",
                        attributes={"operation": operation, "object": object_type},
                    )
                    raise
                finally:
                    record_duration(
                        "modelsentry.storage.duration",
                        (time.perf_counter() - started) * 1000,
                        attributes={"operation": operation, "object": object_type},
                    )

        return wrapper  # type: ignore[return-value]

    return decorator


def instrument_drift(fn: F) -> F:
    """Instrument aggregate-only drift detection while preserving exceptions."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        with span("modelsentry.drift", {"modelsentry.operation": "detect"}) as current:
            try:
                result = fn(*args, **kwargs)
                set_attributes(
                    current,
                    {
                        "modelsentry.outcome": "success",
                        "modelsentry.drift.severity": getattr(
                            result, "overall_severity", None
                        ),
                        "modelsentry.drift.feature_count": len(
                            getattr(result, "feature_results", {})
                        ),
                        "modelsentry.drift.missing_current_count": len(
                            getattr(result, "missing_in_current", ())
                        ),
                        "modelsentry.drift.missing_baseline_count": len(
                            getattr(result, "missing_in_baseline", ())
                        ),
                    },
                )
                record_counter(
                    "modelsentry.drift.evaluations",
                    attributes={
                        "outcome": "success",
                        "severity": getattr(result, "overall_severity", "unknown"),
                    },
                )
                return result
            except Exception as exc:
                record_error(current, exc, stage="drift")
                record_counter(
                    "modelsentry.drift.evaluations",
                    attributes={"outcome": "error", "severity": "unknown"},
                )
                raise
            finally:
                record_duration(
                    "modelsentry.drift.duration", (time.perf_counter() - started) * 1000
                )

    return wrapper  # type: ignore[return-value]


def instrument_fastapi(app: Any) -> None:
    """Attach standard FastAPI HTTP instrumentation when its extra is installed."""
    if not _OTEL_API_AVAILABLE or not _enabled():
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        def scrub_request_url(span: Any, _scope: Mapping[str, Any]) -> None:
            """Remove query values from attributes populated by ASGI."""
            if span is None:
                return
            try:
                attributes = getattr(span, "attributes", {})
                for key in ("http.target", "url.full"):
                    value = attributes.get(key)
                    if isinstance(value, str) and "?" in value:
                        span.set_attribute(key, value.split("?", 1)[0])
                if "url.query" in attributes:
                    span.set_attribute("url.query", "")
            except Exception:
                return

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health",
            server_request_hook=scrub_request_url,
        )
    except Exception:
        return


def configure_telemetry() -> bool:
    """Optionally configure OTLP providers for a standalone process.

    Existing non-default global providers are never replaced.  Returns True
    only when providers were configured; applications may instead configure
    providers themselves and ModelSentry will use them automatically.
    """
    # A library process should not unexpectedly attempt to connect to a local
    # collector merely because the optional dependencies are installed. An
    # endpoint (or explicit enablement) opts standalone setup in.
    if (
        not _OTEL_API_AVAILABLE
        or not _enabled()
        or (
            "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
            and "MODELSENTRY_OTEL_ENABLED" not in os.environ
        )
    ):
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError:
        return False
    configured = False
    try:
        resource = Resource.create(
            {"service.name": os.getenv("OTEL_SERVICE_NAME", "modelsentry")}
        )
        provider = _trace.get_tracer_provider()
        if provider.__class__.__name__ == "ProxyTracerProvider":
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            _trace.set_tracer_provider(provider)
            configured = True
        meter_provider = _metrics.get_meter_provider()
        if meter_provider.__class__.__name__ in {
            "NoOpMeterProvider",
            "ProxyMeterProvider",
        }:
            reader = PeriodicExportingMetricReader(OTLPMetricExporter())
            _metrics.set_meter_provider(
                MeterProvider(resource=resource, metric_readers=[reader])
            )
            configured = True
    except Exception:
        return False
    return configured


def shutdown_telemetry(timeout_millis: int = 5_000) -> None:
    """Best-effort flush of active providers for short-lived processes."""
    if not _OTEL_API_AVAILABLE:
        return
    for provider_getter in (
        getattr(_trace, "get_tracer_provider", None),
        getattr(_metrics, "get_meter_provider", None),
    ):
        try:
            provider = provider_getter() if provider_getter is not None else None
            force_flush = getattr(provider, "force_flush", None)
            if force_flush is not None:
                force_flush(timeout_millis)
        except Exception:
            continue


__all__ = [
    "configure_telemetry",
    "current_context",
    "include_model_id",
    "instrument_fastapi",
    "instrument_storage",
    "model_attributes",
    "record_counter",
    "record_duration",
    "record_error",
    "record_value",
    "set_attributes",
    "span",
    "span_links",
    "shutdown_telemetry",
]
