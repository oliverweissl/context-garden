import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from harness.types import BenchmarkRecord  # noqa: E402


def test_total_tokens_sums_all_five_fields():
    record = BenchmarkRecord(
        task_id="t",
        component="c",
        level="file",
        condition="baseline",
        model="m",
        success=True,
        verification_success=True,
        verification_notes="",
        input_tokens=100,
        repository_tokens=200,
        tool_result_tokens=50,
        skill_tokens=25,
        output_tokens=10,
    )
    assert record.total_tokens == 385
