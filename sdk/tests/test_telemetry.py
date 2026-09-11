"""Tests for optional OpenTelemetry behavior and privacy boundaries."""

from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pandas as pd
import pytest
import modelsentry as ms
from modelsentry import telemetry
from modelsentry.monitor import flush, shutdown


class _RecordingSpan:
    def __init__(self, attributes):
        self.attributes = dict(attributes or {})

    def set_attribute(self, key, value):
        self.attributes[key] = value


def test_noop_when_otel_is_unavailable(monkeypatch):
    monkeypatch.setattr(telemetry, "_OTEL_API_AVAILABLE", False)
    assert telemetry.configure_telemetry() is False
    with telemetry.span("test") as current:
        current.set_attribute("safe", "value")


def test_capture_telemetry_contains_aggregate_data_only(monkeypatch):
    spans = []

    @contextmanager
    def recording_span(name, attributes=None, *, links=None):
        current = _RecordingSpan(attributes)
        spans.append((name, current))
        yield current

    monkeypatch.setattr(telemetry, "span", recording_span)
    monkeypatch.setattr(telemetry, "record_counter", lambda *a, **k: None)
    monkeypatch.setattr(telemetry, "record_duration", lambda *a, **k: None)
    monkeypatch.setattr(telemetry, "current_context", lambda: None)
    monkeypatch.setattr(telemetry, "span_links", lambda contexts: [])

    captured = []
    ms.init(
        model_id="private-model",
        profile_window=1,
        profile_handler=lambda p, m: captured.append(p),
    )
    try:

        @ms.monitor()
        def predict(features):
            return np.array([42.0])

        predict(pd.DataFrame({"secret_feature": [99.0]}))
        flush()
    finally:
        shutdown()

    assert captured
    assert spans
    serialized = repr([(name, current.attributes) for name, current in spans])
    assert "99.0" not in serialized
    assert "42.0" not in serialized
    assert "secret_feature" not in serialized
    assert "private-model" not in serialized


def test_model_id_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("MODELSENTRY_OTEL_INCLUDE_MODEL_ID", raising=False)
    assert telemetry.model_attributes("private-model") == {}
    assert telemetry.model_attributes("private model", explicit=True) == {
        "modelsentry.model_id": "private_model"
    }


def test_real_span_does_not_record_exception_message(monkeypatch):
    trace_sdk = pytest.importorskip("opentelemetry.sdk.trace")
    export_sdk = pytest.importorskip("opentelemetry.sdk.trace.export")
    in_memory = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter"
    )

    exporter = in_memory.InMemorySpanExporter()
    provider = trace_sdk.TracerProvider()
    provider.add_span_processor(export_sdk.SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", lambda: provider.get_tracer("test"))

    secret = "customer-value-must-not-be-exported"
    with pytest.raises(ValueError, match=secret):
        with telemetry.span("modelsentry.test"):
            raise ValueError(secret)

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].events == ()
    assert secret not in repr(spans[0])


def test_span_links_are_bounded(monkeypatch):
    pytest.importorskip("opentelemetry.trace")

    class SpanContext:
        def __init__(self, value):
            self.trace_id = value
            self.span_id = value
            self.is_valid = True

    class Span:
        def __init__(self, value):
            self.value = value

        def get_span_context(self):
            return SpanContext(self.value)

    monkeypatch.setattr(
        telemetry._trace, "get_current_span", lambda context: Span(context)
    )
    monkeypatch.setattr(telemetry, "_Link", lambda span_context: span_context)

    links = telemetry.span_links(list(range(1, 30)))
    assert len(links) == 16


def test_profile_span_links_to_capture_context(monkeypatch):
    trace_sdk = pytest.importorskip("opentelemetry.sdk.trace")
    export_sdk = pytest.importorskip("opentelemetry.sdk.trace.export")
    in_memory = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter"
    )

    exporter = in_memory.InMemorySpanExporter()
    provider = trace_sdk.TracerProvider()
    provider.add_span_processor(export_sdk.SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", lambda: provider.get_tracer("test"))
    monkeypatch.setattr(telemetry, "meter", lambda: None)

    ms.init(model_id="private-model", profile_window=1, profile_handler=lambda *_: None)
    try:

        @ms.monitor()
        def predict(features):
            return np.array([42.0])

        predict(pd.DataFrame({"secret_feature": [99.0]}))
        flush()
    finally:
        shutdown()

    spans = {item.name: item for item in exporter.get_finished_spans()}
    capture = spans["modelsentry.capture"]
    profile = spans["modelsentry.profile"]
    assert len(profile.links) == 1
    assert profile.links[0].context.span_id == capture.context.span_id
    serialized = repr(exporter.get_finished_spans())
    assert "secret_feature" not in serialized
    assert "private-model" not in serialized
