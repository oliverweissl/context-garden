"""Offline, deterministic tests of the benchmark harness wiring.

These never invoke a real agent (see FakeRunner) -- they prove that
fixture materialization, the .claude/skills/ vs preseeded-AGENTS.md
distinction between baseline/treatment, and each verify type actually
work against the real fixtures under benchmarks/fixtures/, using a
scripted stand-in for "what the agent did" instead of an API call.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "benchmarks"))

from harness.fixtures import discover_tasks, materialize  # noqa: E402
from harness.run import run_condition  # noqa: E402
from harness.runners import FakeRunner  # noqa: E402
from harness.types import AgentRunResult  # noqa: E402
from harness.verify import run_verification  # noqa: E402

TASKS = {t.task_id: t for t in discover_tasks()}


def test_discovers_all_five_fixtures():
    assert set(TASKS) == {
        "pruner-file-tokenusage-bug",
        "trellis-toy-solver-order-regression",
        "compost-noisy-failure-cluster",
        "seedbank-recurring-facts",
        "weeder-bloated-skill-audit",
    }


@pytest.mark.parametrize("task_id", list(TASKS))
def test_every_task_names_a_relevant_skill_directory(task_id):
    task = TASKS[task_id]
    for name in task.skill_relevance:
        assert (REPO_ROOT / "skills" / name / "SKILL.md").exists()


def _do_nothing(prompt: str, workdir: Path) -> AgentRunResult:
    return AgentRunResult(success=True)


def _run(task, condition, behavior, tmp_path):
    return run_condition(task, condition, FakeRunner(behavior), workdir_base=tmp_path)


class TestPruner:
    task = TASKS["pruner-file-tokenusage-bug"]

    def test_patch_breaks_the_target_test(self, tmp_path):
        dest = tmp_path / "baseline"
        dest.mkdir()
        materialize(self.task, "baseline", dest)
        ok, _ = run_verification(self.task, dest)
        assert ok is False

    def test_treatment_installs_pruner_skill_baseline_does_not(self, tmp_path):
        treat = tmp_path / "treatment"
        treat.mkdir()
        materialize(self.task, "treatment", treat)
        assert (treat / ".claude" / "skills" / "pruner" / "SKILL.md").exists()

        base = tmp_path / "baseline"
        base.mkdir()
        materialize(self.task, "baseline", base)
        assert not (base / ".claude").exists()

    def test_correct_fix_passes_verification(self, tmp_path):
        def fix(prompt: str, workdir: Path) -> AgentRunResult:
            target = workdir / "benchmarks" / "harness" / "types.py"
            text = target.read_text()
            fixed = text.replace(
                "            + self.skill_tokens\n        )",
                "            + self.skill_tokens\n            + self.output_tokens\n        )",
            )
            assert fixed != text
            target.write_text(fixed)
            return AgentRunResult(success=True)

        record = _run(self.task, "baseline", fix, tmp_path)
        assert record.verification_success is True

    def test_no_op_fails_verification(self, tmp_path):
        record = _run(self.task, "baseline", _do_nothing, tmp_path)
        assert record.verification_success is False


class TestTrellis:
    task = TASKS["trellis-toy-solver-order-regression"]

    def test_correct_verdict_passes(self, tmp_path):
        def verdict_regression(prompt: str, workdir: Path) -> AgentRunResult:
            path = workdir / "examples" / "toy_heat_solver" / "VERDICT.txt"
            path.write_text("VERDICT: REGRESSION -- one-sided stencil is only O(h).\n")
            return AgentRunResult(success=True)

        record = _run(self.task, "treatment", verdict_regression, tmp_path)
        assert record.verification_success is True

    def test_wrong_verdict_fails(self, tmp_path):
        def verdict_correct(prompt: str, workdir: Path) -> AgentRunResult:
            path = workdir / "examples" / "toy_heat_solver" / "VERDICT.txt"
            path.write_text("VERDICT: CORRECT -- test still passes.\n")
            return AgentRunResult(success=True)

        record = _run(self.task, "treatment", verdict_correct, tmp_path)
        assert record.verification_success is False

    def test_missing_verdict_file_fails(self, tmp_path):
        record = _run(self.task, "baseline", _do_nothing, tmp_path)
        assert record.verification_success is False


class TestCompost:
    task = TASKS["compost-noisy-failure-cluster"]

    def test_fixing_both_bugs_and_naming_root_cause_passes(self, tmp_path):
        def fix(prompt: str, workdir: Path) -> AgentRunResult:
            flaky = workdir / "examples" / "flaky_service"
            service = flaky / "service.py"
            text = service.read_text()
            text = text.replace(
                '_TIMEOUTS.get("default_typo", "30")', '_TIMEOUTS.get("default", 30)'
            )
            text = text.replace('"high": 2}', '"high": 2, "urgent": 3}')
            service.write_text(text)
            (flaky / "ROOT_CAUSE.txt").write_text("get_timeout\n")
            return AgentRunResult(success=True)

        record = _run(self.task, "treatment", fix, tmp_path)
        assert record.verification_success is True, record.verification_notes

    def test_unfixed_suite_fails(self, tmp_path):
        record = _run(self.task, "baseline", _do_nothing, tmp_path)
        assert record.verification_success is False


class TestSeedbank:
    task = TASKS["seedbank-recurring-facts"]

    def test_treatment_preseeds_agents_md_baseline_does_not(self, tmp_path):
        treat = tmp_path / "treatment"
        treat.mkdir()
        materialize(self.task, "treatment", treat)
        assert (treat / "AGENTS.md").exists()
        assert not (treat / ".claude").exists()

        base = tmp_path / "baseline"
        base.mkdir()
        materialize(self.task, "baseline", base)
        assert not (base / "AGENTS.md").exists()

    def test_complete_answers_pass(self, tmp_path):
        def answer(prompt: str, workdir: Path) -> AgentRunResult:
            (workdir / "ANSWERS.txt").write_text(
                "1. scripts/validate-skills (docs/skill-development.md)\n"
                "2. A path must never point outside a Skill's own directory,"
                " e.g. ../../benchmarks (docs/architecture.md)\n"
                "3. routing accuracy after >= before AND constraint"
                " preservation is 100% (skills/weeder/SKILL.md)\n"
            )
            return AgentRunResult(success=True)

        record = _run(self.task, "treatment", answer, tmp_path)
        assert record.verification_success is True

    def test_partial_answers_fail(self, tmp_path):
        def partial(prompt: str, workdir: Path) -> AgentRunResult:
            (workdir / "ANSWERS.txt").write_text("1. scripts/validate-skills\n")
            return AgentRunResult(success=True)

        record = _run(self.task, "baseline", partial, tmp_path)
        assert record.verification_success is False


class TestWeeder:
    task = TASKS["weeder-bloated-skill-audit"]

    def test_real_weeder_optimize_pass_satisfies_verification(self, tmp_path):
        weeder_cli = REPO_ROOT / "skills" / "weeder" / "scripts" / "weeder.py"

        def apply_weeder_optimize(prompt: str, workdir: Path) -> AgentRunResult:
            skill_dir = workdir / "examples" / "demo-skill"
            optimized = workdir / "examples" / "demo-skill-optimized"
            subprocess.run(
                [
                    sys.executable,
                    str(weeder_cli),
                    "optimize",
                    str(skill_dir),
                    "--out",
                    str(optimized),
                ],
                check=True,
                capture_output=True,
            )
            shutil.rmtree(skill_dir)
            shutil.move(str(optimized), str(skill_dir))
            return AgentRunResult(success=True)

        record = _run(self.task, "baseline", apply_weeder_optimize, tmp_path)
        assert record.verification_success is True, record.verification_notes

    def test_no_op_fails_reduction_bar(self, tmp_path):
        record = _run(self.task, "baseline", _do_nothing, tmp_path)
        assert record.verification_success is False
