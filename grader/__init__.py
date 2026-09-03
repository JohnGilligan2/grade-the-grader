"""grade-the-grader: calibrate an LLM judge before trusting it.

Four pieces, designed to be copied into a project rather than installed:

  judge.py      structured judge with an escape hatch (stub, rubric, or API)
  calibrate.py  judge-vs-human agreement, with direction-of-miss analysis
  checks.py     deterministic rules that run alongside the judge
  gate.py       regression gate: replays, pass@k vs pass^k, beat-or-lose

Stdlib only. The `anthropic` package is imported lazily and only by
AnthropicJudge; everything else, including the example and tests, runs
offline.
"""

from .calibrate import CalibrationReport, Disagreement, calibrate
from .checks import CheckResult, all_passed, forbid, max_chars, require_exactly, run_checks
from .gate import GateDecision, RunResult, evaluate, gate
from .judge import Judge, RubricJudge, Verdict, render_case

__all__ = [
    "CalibrationReport", "Disagreement", "calibrate",
    "CheckResult", "all_passed", "forbid", "max_chars", "require_exactly", "run_checks",
    "GateDecision", "RunResult", "evaluate", "gate",
    "Judge", "RubricJudge", "Verdict", "render_case",
]
