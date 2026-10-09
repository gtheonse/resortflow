"""T010 — contract-conformance tests for `observability/telemetry.py`
(`contracts/telemetry-module-contract.md`): both namespaces always emitted
together, PII masking applied before any attribute is set, and
`gen_ai.system` is never a hardcoded literal."""

from __future__ import annotations

import pytest

from observability import telemetry


@pytest.fixture(autouse=True)
def _isolated_tracer():
    exporter = telemetry.use_in_memory_exporter_for_tests()
    yield exporter
    telemetry.reset_for_tests()


def test_start_agent_span_emits_all_three_namespaces(_isolated_tracer):
    with telemetry.start_agent_span("test-span", node="my_node", domain="dining"):
        pass
    (span,) = _isolated_tracer.get_finished_spans()
    attrs = dict(span.attributes)
    assert attrs["gen_ai.operation.name"] == "my_node"
    assert "gen_ai.system" in attrs
    assert attrs["langfuse.trace.metadata.node"] == "my_node"
    assert attrs["resortflow.node"] == "my_node"
    assert attrs["resortflow.domain"] == "dining"


def test_gen_ai_system_reads_from_config_not_a_literal(_isolated_tracer, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "some-other-provider")
    with telemetry.start_agent_span("test-span", node="my_node"):
        pass
    (span,) = _isolated_tracer.get_finished_spans()
    assert dict(span.attributes)["gen_ai.system"] == "some-other-provider"


def test_gen_ai_system_defaults_to_current_build_provider(_isolated_tracer, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with telemetry.start_agent_span("test-span", node="my_node"):
        pass
    (span,) = _isolated_tracer.get_finished_spans()
    assert dict(span.attributes)["gen_ai.system"] == "azure-foundry"


def test_set_payload_masks_pii_before_setting_any_attribute(_isolated_tracer):
    with telemetry.start_agent_span("test-span", node="my_node") as span:
        telemetry.set_payload(span, "input", "Call me at 555-123-4567, I'm John Smith")
    (finished,) = _isolated_tracer.get_finished_spans()
    attrs = dict(finished.attributes)
    assert "555-123-4567" not in attrs["gen_ai.prompt"]
    assert "555-123-4567" not in attrs["langfuse.observation.input"]


def test_set_payload_truncates_oversized_payload_with_marker(_isolated_tracer):
    huge = "x" * (telemetry.LARGE_PAYLOAD_THRESHOLD + 100)
    with telemetry.start_agent_span("test-span", node="my_node") as span:
        telemetry.set_payload(span, "output", huge)
    (finished,) = _isolated_tracer.get_finished_spans()
    attrs = dict(finished.attributes)
    assert len(attrs["gen_ai.completion"]) == telemetry.LARGE_PAYLOAD_THRESHOLD
    assert attrs["resortflow.output.truncated"] is True


def test_set_payload_no_marker_when_within_threshold(_isolated_tracer):
    with telemetry.start_agent_span("test-span", node="my_node") as span:
        telemetry.set_payload(span, "context", "short context")
    (finished,) = _isolated_tracer.get_finished_spans()
    assert dict(finished.attributes)["resortflow.context.truncated"] is False
