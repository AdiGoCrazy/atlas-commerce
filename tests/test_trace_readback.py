"""Trace readback sanitizes content before it reaches the assistant."""

from adaptive_commerce.integrations.trace_readback import _sanitize_spans


def test_trace_summary_keeps_status_but_discards_prompt_and_evidence():
    spans = _sanitize_spans({"spans": [{
        "name": "signal_extraction",
        "status": {"status_code": "ERROR"},
        "attributes": {
            "neatlogs.span.kind": "AGENT",
            "exception.type": "TimeoutError",
            "llm.input_messages": "private user evidence",
            "llm.output_messages": "private model response",
        },
    }]})

    assert len(spans) == 1
    assert spans[0].name == "signal_extraction"
    assert spans[0].status == "ERROR"
    assert spans[0].error_type == "TimeoutError"
    assert "private" not in spans[0].model_dump_json()
