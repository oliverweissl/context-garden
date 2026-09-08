import pytest
from service import priority_rank, process


@pytest.mark.parametrize("item_id", range(15))
def test_process_returns_ok(item_id):
    result = process({"id": item_id, "kind": "default"})
    assert result["status"] == "ok"


def test_process_batch_kind_is_fine():
    result = process({"id": 999, "kind": "batch"})
    assert result["timeout"] == 120


@pytest.mark.parametrize("priority", ["low", "normal", "high", "urgent", "urgent"])
def test_priority_rank_is_an_int(priority):
    assert isinstance(priority_rank({"priority": priority}), int)
