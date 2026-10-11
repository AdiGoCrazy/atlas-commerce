from contextlib import contextmanager

from adaptive_commerce.integrations import observability


def test_trace_step_records_workflow_ids_without_capturing_payload(monkeypatch):
    recorded = {"attributes": {}, "name": None, "kind": None}

    class Span:
        def set_attribute(self, name, value):
            recorded["attributes"][name] = value

    @contextmanager
    def fake_trace(name, *, kind):
        recorded["name"] = name
        recorded["kind"] = kind
        yield Span()

    monkeypatch.setattr(observability.neatlogs, "trace", fake_trace)

    with observability.trace_step(
        "CHAIN", "recommendation_ranking", region_id="r1",
        cycle_id="c1", version_id="v1",
        attributes={"result_status": "activated", "signal_count": 3},
    ):
        pass

    assert recorded == {
        "name": "recommendation_ranking",
        "kind": "CHAIN",
        "attributes": {
            "atlas.region_id": "r1",
            "atlas.cycle_id": "c1",
            "atlas.version_id": "v1",
            "atlas.result_status": "activated",
            "atlas.signal_count": 3,
        },
    }


def test_trace_step_preserves_workflow_errors(monkeypatch):
    @contextmanager
    def fake_trace(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(observability.neatlogs, "trace", fake_trace)

    try:
        with observability.trace_step("AGENT", "signal_extraction"):
            raise RuntimeError("signal extraction failed")
    except RuntimeError as error:
        assert str(error) == "signal extraction failed"
    else:
        raise AssertionError("trace_step must not swallow workflow failures")
