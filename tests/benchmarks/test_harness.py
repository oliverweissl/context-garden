"""Offline tests of fixture materialization and each verify type against
the real benchmarks/fixtures/, with FakeRunner scripting the agent's edits.
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


def test_discovers_all_fixtures():
    assert set(TASKS) == {
        "pruner-file-tokenusage-bug",
        "mycelium-overlapping-investigation",
        "mycelium-notes-reuse",
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
        materialize(self.task, "treatment-natural", treat)
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
                "            + self.skill_tokens\n            + self.subagent_tokens\n",
                "            + self.skill_tokens\n            + self.output_tokens\n"
                "            + self.subagent_tokens\n",
            )
            assert fixed != text
            target.write_text(fixed)
            return AgentRunResult(success=True)

        record = _run(self.task, "baseline", fix, tmp_path)
        assert record.verification_success is True

    def test_no_op_fails_verification(self, tmp_path):
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
            (flaky / "ROOT_CAUSE.txt").write_text("ROOT_CAUSE: get_timeout\n")
            return AgentRunResult(success=True)

        record = _run(self.task, "treatment-natural", fix, tmp_path)
        assert record.verification_success is True, record.verification_notes

    def test_unfixed_suite_fails(self, tmp_path):
        record = _run(self.task, "baseline", _do_nothing, tmp_path)
        assert record.verification_success is False


class TestSeedbank:
    task = TASKS["seedbank-recurring-facts"]

    def test_treatment_preseeds_agents_md_baseline_does_not(self, tmp_path):
        treat = tmp_path / "treatment"
        treat.mkdir()
        materialize(self.task, "treatment-natural", treat)
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

        record = _run(self.task, "treatment-natural", answer, tmp_path)
        assert record.verification_success is True

    def test_equivalent_path_phrasing_passes(self, tmp_path):
        def answer(prompt: str, workdir: Path) -> AgentRunResult:
            (workdir / "ANSWERS.txt").write_text(
                "1. scripts/validate-skills (docs/skill-development.md)\n"
                "2. It must never point outside the Skill's own directory"
                " (docs/architecture.md)\n"
                "3. routing accuracy after >= before and 100% constraint"
                " preservation (skills/weeder/SKILL.md)\n"
            )
            return AgentRunResult(success=True)

        record = _run(self.task, "treatment-natural", answer, tmp_path)
        assert record.verification_success is True

    def test_wrong_path_answer_fails(self, tmp_path):
        def wrong(prompt: str, workdir: Path) -> AgentRunResult:
            (workdir / "ANSWERS.txt").write_text(
                "1. scripts/validate-skills\n"
                "2. It must never contain spaces.\n"
                "3. routing accuracy and 100% preservation\n"
            )
            return AgentRunResult(success=True)

        record = _run(self.task, "treatment-natural", wrong, tmp_path)
        assert record.verification_success is False

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



class TestMycelium:
    overlap = TASKS["mycelium-overlapping-investigation"]
    reuse = TASKS["mycelium-notes-reuse"]

    def test_treatment_gets_skill_hooks_and_scout_baseline_does_not(self, tmp_path):
        treat = tmp_path / "treatment"
        treat.mkdir()
        materialize(self.overlap, "treatment-natural", treat)
        assert (treat / ".claude" / "skills" / "mycelium" / "bin" / "mycelium").exists()
        settings = (treat / ".claude" / "settings.json").read_text()
        assert "mycelium/bin/mycelium" in settings and '"Agent"' in settings
        assert (treat / ".claude" / "agents" / "scout.md").exists()

        base = tmp_path / "baseline"
        base.mkdir()
        materialize(self.overlap, "baseline", base)
        assert not (base / ".claude").exists()
        assert not (base / "skills" / "mycelium").exists()

    def test_materialized_gate_hook_denies_a_vague_brief(self, tmp_path):
        import json

        treat = tmp_path / "treatment"
        treat.mkdir()
        materialize(self.overlap, "treatment-natural", treat)
        settings = json.loads((treat / ".claude" / "settings.json").read_text())
        command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        payload = {"session_id": "s", "cwd": str(treat), "tool_name": "Agent",
                   "tool_input": {"description": "look", "prompt": "explore the stores"}}
        proc = subprocess.run(
            ["bash", "-c", command], input=json.dumps(payload), capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "CLAUDE_PROJECT_DIR": str(treat),
                 "CONTEXT_GARDEN_MODE_MYCELIUM": "on"},  # what the runner sets
        )
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny"
        assert "`Task:`" in out["permissionDecisionReason"]

    def test_overlay_scout_matches_skill_template(self):
        overlay = self.overlap.fixture_dir / "treatment_overlay" / ".claude" / "agents" / "scout.md"
        template = REPO_ROOT / "skills" / "mycelium" / "agents" / "scout.md"
        assert overlay.read_text() == template.read_text()

    def test_correct_answers_pass(self, tmp_path):
        def answer(prompt: str, workdir: Path) -> AgentRunResult:
            (workdir / "ANSWERS.txt").write_text(
                "COMPOST_KEEP: 10\nCOMPOST_MB: 200\nSEEDBANK_LOCK: fcntl.flock\n"
                "SEEDBANK_WINDOWS: no-op (no locking)\nPRUNER_WRITE: os.replace\nVERSION: 0.2.0\n"
            )
            return AgentRunResult(success=True)

        assert _run(self.overlap, "treatment-natural", answer, tmp_path).verification_success

    def test_wrong_or_missing_answers_fail(self, tmp_path):
        def wrong(prompt: str, workdir: Path) -> AgentRunResult:
            (workdir / "ANSWERS.txt").write_text(
                "COMPOST_KEEP: 20\nCOMPOST_MB: 200\nSEEDBANK_LOCK: fcntl.flock\n"
                "SEEDBANK_WINDOWS: no-op\nPRUNER_WRITE: os.rename\nVERSION: 0.2.0\n"
            )
            return AgentRunResult(success=True)

        assert not _run(self.overlap, "baseline", wrong, tmp_path).verification_success
        assert not _run(self.overlap, "baseline", _do_nothing, tmp_path).verification_success

    def test_answers_are_true_of_the_repo(self):
        # the fixture's expected values must track the code they describe
        co = (REPO_ROOT / "skills/compost/scripts/co_store.py").read_text()
        assert '{"keep_per_command": 10, "max_store_mb": 200}' in co and "def gc(" in co
        assert "fcntl.flock" in (REPO_ROOT / "skills/seedbank/scripts/sb_store.py").read_text()
        assert "os.replace" in (REPO_ROOT / "skills/pruner/scripts/pr_select.py").read_text()
        assert 'version = "0.2.0"' in (REPO_ROOT / "pyproject.toml").read_text()

    def test_followup_runs_second_session_and_sums_usage(self, tmp_path):
        prompts = []

        def two_sessions(prompt: str, workdir: Path) -> AgentRunResult:
            prompts.append(prompt)
            out = workdir / "RETENTION.txt"
            if len(prompts) == 1:
                out.write_text("RETENTION_FUNCTION: Store.gc\nDEFAULT_KEEP: 10\nDEFAULT_MB: 200\n")
            else:
                out.write_text(out.read_text() + "LATEST_DELETABLE: no\nLOCK_CALL: fcntl.flock\n")
            return AgentRunResult(
                success=True, input_tokens=100, output_tokens=10, subagent_count=1,
                subagent_tokens=500,
            )

        record = _run(self.reuse, "treatment-natural", two_sessions, tmp_path)
        assert len(prompts) == 2 and "follow-up" in prompts[1]
        assert record.verification_success is True
        assert record.sessions == 2
        assert record.input_tokens == 200
        assert (record.subagent_count, record.subagent_tokens) == (2, 1000)
        assert record.total_tokens == 200 + 20 + 1000

    def test_first_session_alone_fails(self, tmp_path):
        def only_first(prompt: str, workdir: Path) -> AgentRunResult:
            if "follow-up" not in prompt:
                (workdir / "RETENTION.txt").write_text(
                    "RETENTION_FUNCTION: gc\nDEFAULT_KEEP: 10\nDEFAULT_MB: 200\n"
                )
            return AgentRunResult(success=True)

        assert not _run(self.reuse, "baseline", only_first, tmp_path).verification_success


def test_subagent_usage_counts_each_message_once(tmp_path):
    from harness.runners import _subagent_usage

    transcript = tmp_path / "sess.jsonl"
    transcript.write_text("")
    sub = tmp_path / "sess" / "subagents"
    sub.mkdir(parents=True)
    line = (
        '{"message": {"id": "m1", "usage": {"input_tokens": 5, '
        '"cache_creation_input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 999}}}'
    )
    (sub / "agent-a.jsonl").write_text(line + "\n" + line + "\n")  # same message repeated
    (sub / "agent-b.jsonl").write_text(line.replace("m1", "m2") + "\n")
    assert _subagent_usage(transcript) == (2, 250)

@pytest.mark.parametrize("condition", ["baseline", "treatment-natural", "treatment-forced"])
def test_working_copy_excludes_answers_and_skill_under_test(condition, tmp_path):
    task = TASKS["seedbank-recurring-facts"]
    dest = tmp_path / condition
    dest.mkdir()
    materialize(task, condition, dest)
    assert not (dest / "benchmarks" / "fixtures").exists()
    assert not (dest / "tests" / "benchmarks" / "test_harness.py").exists()
    assert not (dest / "examples").exists()
    assert not (dest / "skills" / "seedbank").exists()
    # plugin manifest/hooks never reach any arm (seedbank's PostToolUse hooks)
    assert not (dest / "hooks").exists()
    assert not (dest / ".claude-plugin").exists()
    # other skills stay as ordinary repo content (some fixtures' answers live there)
    assert (dest / "skills" / "weeder" / "SKILL.md").exists()
    assert (dest / "benchmarks" / "harness" / "types.py").exists()
