from context_garden.core.artifacts import ArtifactStore, content_id


def test_content_id_is_stable():
    assert content_id(b"hello") == content_id(b"hello")
    assert content_id(b"hello") != content_id(b"world")


def test_put_and_get_roundtrip(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.put(b"some large tool output", media_type="text/plain")

    assert store.exists(ref.artifact_id)
    assert store.get(ref.artifact_id) == b"some large tool output"
    assert ref.size_bytes == len(b"some large tool output")


def test_put_is_content_addressed_and_deduplicates(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    ref1 = store.put(b"same content")
    ref2 = store.put(b"same content")

    assert ref1.artifact_id == ref2.artifact_id


def test_get_missing_artifact_raises(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    try:
        store.get("does-not-exist")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")
