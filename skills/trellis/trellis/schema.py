"""Report schema: the machine-readable evidence contract every check
produces, and how individual CheckResults aggregate into a Report.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


_SEVERITY = {Status.PASS: 0, Status.WARN: 1, Status.FAIL: 2}

# The four tiers the "Important Rule" requires distinguishing: passing
# tests is *implementation* evidence, not automatically *numerical*,
# *model_validation*, or *empirical* evidence. See references/modules.md.
CATEGORIES = ("implementation", "numerical", "model_validation", "empirical")


@dataclass
class CheckResult:
    name: str
    status: str          # PASS | WARN | FAIL
    category: str         # one of CATEGORIES
    metric: dict
    expected: object
    observed: object
    evidence: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        from ._util import to_jsonable
        self.metric = to_jsonable(self.metric)
        self.expected = to_jsonable(self.expected)
        self.observed = to_jsonable(self.observed)
        self.evidence = to_jsonable(self.evidence)

    def to_dict(self) -> dict:
        return {
            "name": self.name, "status": self.status, "category": self.category,
            "metric": self.metric, "expected": self.expected, "observed": self.observed,
            "evidence": self.evidence, "notes": self.notes,
        }


@dataclass
class Report:
    status: str
    checks: list
    unsupported_claims: list
    remaining_risks: list
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "checks": [c.to_dict() for c in self.checks],
            "unsupported_claims": self.unsupported_claims,
            "remaining_risks": self.remaining_risks,
            "created_at": self.created_at,
        }

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    def render_human(self) -> str:
        lines = [f"status: {self.status}", "", "checks:"]
        for c in self.checks:
            lines.append(f"  [{c.status:4s}] {c.name}  ({c.category})")
            lines.append(f"          expected={c.expected!r}  observed={c.observed!r}")
            if c.notes:
                lines.append(f"          note: {c.notes}")
        if self.unsupported_claims:
            lines.append("")
            lines.append("unsupported_claims:")
            lines += [f"  - {u}" for u in self.unsupported_claims]
        if self.remaining_risks:
            lines.append("")
            lines.append("remaining_risks:")
            lines += [f"  - {r}" for r in self.remaining_risks]
        return "\n".join(lines)

    def exit_code(self) -> int:
        return 1 if self.status == Status.FAIL.value else 0


def build_report(results: list, unsupported_claims: list | None = None,
                  remaining_risks: list | None = None, auto_gaps: bool = True) -> Report:
    """Aggregates CheckResults into a Report. Overall status is the worst
    of any individual check (any FAIL -> FAIL; else any WARN -> WARN; else
    PASS). With auto_gaps=True (default), automatically appends
    remaining_risks/unsupported_claims for verification *tiers that were
    never exercised* -- this is what stops "unit tests passed" from being
    silently read as "numerically correct": if nothing in `results` has
    category='model_validation', the report says so explicitly, rather
    than relying on whoever wrote the check suite to remember to disclose it."""
    unsupported_claims = list(unsupported_claims or [])
    remaining_risks = list(remaining_risks or [])
    categories_seen = {c.category for c in results}

    if auto_gaps:
        if not results:
            unsupported_claims.append(
                "No checks were executed at all -- no claim of numerical correctness is supported by this report."
            )
        if "model_validation" not in categories_seen:
            remaining_risks.append(
                "No model/reference-solution validation was performed -- the numerics may be internally "
                "self-consistent (correct residuals, correct convergence order) while the underlying model "
                "or discretization still does not represent the intended physics/problem correctly."
            )
        if "empirical" not in categories_seen:
            remaining_risks.append(
                "No stochastic replication was performed -- any reported improvement or result may not hold "
                "across random seeds/trials and should not be claimed as robust from this report alone."
            )
        if "numerical" not in categories_seen and results:
            unsupported_claims.append(
                "No numerical-correctness checks (residuals, convergence order, conditioning, ...) were run -- "
                "only implementation/empirical checks. Do not claim numerical correctness from this report."
            )

    overall = Status.PASS
    for c in results:
        s = Status(c.status)
        if _SEVERITY[s] > _SEVERITY[overall]:
            overall = s

    return Report(status=overall.value, checks=list(results),
                  unsupported_claims=unsupported_claims, remaining_risks=remaining_risks)
