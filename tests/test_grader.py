"""Offline tests. Run: python tests/test_grader.py

Standalone script style, no pytest dependency: each check prints PASS/FAIL
and the script exits nonzero on any failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grader import (RubricJudge, Verdict, calibrate, evaluate, forbid, gate,
                    max_chars, require_exactly, run_checks)

FAILURES = []
RAN = 0


def check(name, cond, detail=""):
    global RAN
    RAN += 1
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# --- calibrate -------------------------------------------------------------

human = {"a": (5, ""), "b": (5, ""), "c": (2, ""), "d": (4, "")}

verdicts = [
    Verdict("a", 5, "good"),          # exact agree
    Verdict("b", 1, "harsh"),         # judge harsh (miss)
    Verdict("c", 2, "right"),         # exact agree
    Verdict("d", 3, "borderline"),    # within tolerance 1 -> agree
    Verdict("zz", 1, "not graded"),   # no human verdict -> ignored
]
r = calibrate(verdicts, human)
check("calibrate counts only human-graded cases", r.n == 4, f"n={r.n}")
check("calibrate agreement math", r.agreements == 3, f"agree={r.agreements}")
check("calibrate finds the one miss", len(r.disagreements) == 1)
check("miss direction is judge_harsh", r.disagreements[0].direction == "judge_harsh")
check("two misses are not systematic", not r.systematic)

harsh_all = calibrate(
    [Verdict("a", 1, ""), Verdict("b", 1, ""), Verdict("c", 2, ""), Verdict("d", 1, "")],
    {"a": (5, ""), "b": (5, ""), "c": (5, ""), "d": (4, "")})
check("all-harsh misses flagged systematic", harsh_all.systematic)
check("systematic verdict names the rubric",
      "RUBRIC" in harsh_all.verdict_line())

skipper = calibrate([Verdict("a", 3, "", insufficient=True)], {"a": (5, "")})
check("insufficient_information is skipped, not a disagreement",
      skipper.skipped == 1 and skipper.n == 0)

# --- checks ----------------------------------------------------------------

c1 = forbid("no-claims", [r"\bI have fixed\b"])
res = run_checks([c1], {}, "I have fixed everything.")
check("forbid catches the pattern", not res[0].passed)
res = run_checks([c1], {}, "We plan to fix it tomorrow.")
check("forbid passes clean text", res[0].passed)

c2 = require_exactly("once", r"\[SCAFFOLD\]")
check("require_exactly passes single occurrence",
      c2({}, "[SCAFFOLD] body").passed)
check("require_exactly fails duplicates",
      not c2({}, "[SCAFFOLD] body [SCAFFOLD]").passed)
check("require_exactly fails absence", not c2({}, "body only").passed)

check("max_chars boundary is inclusive", max_chars("len", 5)({}, "12345").passed)
check("max_chars rejects over-limit", not max_chars("len", 5)({}, "123456").passed)

# --- judge -----------------------------------------------------------------

j = RubricJudge("test", [("bad word", lambda c: 1 if "bad" in c["output"] else None)])
check("rubric judge fires first matching rule", j.judge({"id": "x", "output": "bad"}).score == 1)
check("rubric judge default when nothing fires", j.judge({"id": "x", "output": "ok"}).score == 5)

# --- gate ------------------------------------------------------------------

fixtures = [{"id": "f1"}, {"id": "f2"}]
always_good = lambda fx: "good output"
always_bad = lambda fx: "bad output"
score_judge = RubricJudge("scorer", [("bad", lambda c: 1 if "bad" in c["output"] else None)])

perfect = evaluate("perfect", fixtures, always_good, score_judge, [], replays=4)
check("perfect run: per-trial pass rate 1.0", perfect.per_trial_pass_rate == 1.0)
check("perfect run: pass^5 is 1.0", perfect.pass_pow_k(5) == 1.0)

broken = evaluate("broken", fixtures, always_bad, score_judge, [], replays=4)
check("broken run: per-trial pass rate 0.0", broken.per_trial_pass_rate == 0.0)

d = gate(broken, perfect, k=5)
check("gate promotes a strict winner", d.promote)
d = gate(perfect, perfect, k=5)
check("gate rejects a tie", not d.promote)
d = gate(perfect, broken, k=5)
check("gate rejects a loser", not d.promote)

# pass@k vs pass^k arithmetic at p=0.9, k=5
class _Fake:
    per_trial_pass_rate = 0.9
check("pass@5 at p=0.9 is ~0.99999",
      abs((1 - (1 - 0.9) ** 5) - 0.99999) < 1e-9)
check("pass^5 at p=0.9 is ~0.59",
      abs(0.9 ** 5 - 0.59049) < 1e-9)

# checks gate the pass even when the judge is happy
lenient_judge = RubricJudge("lenient", [])   # always 5
claimy = lambda fx: "I have fixed everything."
guarded = evaluate("guarded", fixtures, claimy, lenient_judge,
                   [forbid("no-claims", [r"\bI have fixed\b"])], replays=2)
check("a check failure fails the trial even at judge score 5",
      guarded.per_trial_pass_rate == 0.0)

# ---------------------------------------------------------------------------

print()
if FAILURES:
    print(f"{len(FAILURES)} of {RAN} FAILED: {FAILURES}")
    sys.exit(1)
print(f"All {RAN} checks passed.")
