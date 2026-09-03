"""Deterministic checks that run alongside the judge, never instead of it.

The split of labor:

  * checks.py answers questions with exact answers. Did the output claim a
    completed action? Does the required scaffold appear exactly once? Is a
    forbidden string present? These are string operations; asking a model
    to grade them adds cost and subtracts reliability.

  * judge.py answers questions of quality and alignment, which have no
    exact answer.

A rule of thumb that has held up: any judge critique that recurs verbatim is
a candidate for demotion into a deterministic check. And any deterministic
check that needs three paragraphs of exceptions probably belongs to the judge.

Checks are also the anti-gaming layer of the regression gate (gate.py). A
candidate prompt that learns to please the judge still has to get past rules
that cannot be charmed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def forbid(name: str, patterns, flags=re.IGNORECASE):
    """Fail if any pattern appears in the output.

    The canonical use here is completed-action claims: a customer draft that
    asserts "I have reset your password" on a ticket where nobody has done
    anything is the single worst thing this class of system produces, and it
    is a regex, not a judgment call.
    """
    compiled = [re.compile(p, flags) for p in patterns]

    def check(case: dict, output: str) -> CheckResult:
        for rx in compiled:
            m = rx.search(output)
            if m:
                return CheckResult(name, False, f"matched: {m.group(0)[:60]!r}")
        return CheckResult(name, True, "clean")

    return check


def require_exactly(name: str, pattern: str, count: int = 1, flags=0):
    """Fail unless the pattern appears exactly `count` times.

    Born from a real rubric defect: a designed scaffold header the pipeline
    adds to every draft. The judge coin-flipped between calling it "expected"
    and calling it a "leak". The correct treatment was never a judgment call:
    present exactly once at the top is by design, repeated inside the body is
    a bug. That is a counting problem, so it lives here now and the judge is
    told to ignore the scaffold entirely.
    """
    rx = re.compile(pattern, flags)

    def check(case: dict, output: str) -> CheckResult:
        found = len(rx.findall(output))
        if found == count:
            return CheckResult(name, True, f"count={found}")
        return CheckResult(name, False, f"expected {count}, found {found}")

    return check


def max_chars(name: str, limit: int):
    def check(case: dict, output: str) -> CheckResult:
        ok = len(output) <= limit
        return CheckResult(name, ok, f"{len(output)}/{limit} chars")

    return check


def run_checks(checks, case: dict, output: str):
    """Run every check; never short-circuit.

    A failing first check does not excuse skipping the rest: the full failure
    list is the diagnostic, and partial reports hide co-occurring defects.
    """
    return [c(case, output) for c in checks]


def all_passed(results) -> bool:
    return all(r.passed for r in results)
