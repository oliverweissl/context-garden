from context_garden.core.provenance import ProvenanceRecord, Source


def test_provenance_record_to_dict():
    record = ProvenanceRecord(
        claim="convergence check passes",
        sources=[Source(type="file", uri="src/solver.cpp", content_hash="abc123")],
        generated_by="trellis",
    )
    data = record.to_dict()

    assert data["claim"] == "convergence check passes"
    assert data["sources"] == [
        {"type": "file", "uri": "src/solver.cpp", "content_hash": "abc123"}
    ]
    assert data["generated_by"] == "trellis"
    assert "created_at" in data


def test_provenance_record_defaults_to_no_sources():
    record = ProvenanceRecord(claim="unverified claim")
    assert record.to_dict()["sources"] == []
