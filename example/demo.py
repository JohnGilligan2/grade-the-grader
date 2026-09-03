"""Offline demo: the whole methodology on synthetic data, no API key.

This recreates, with fake tickets, the exact sequence that happened in
production:

  1. An LLM judge grades ten cases. Its rubric contains two defects the
     author does not know about.
  2. A human grades the same ten cases blind.
  3. calibrate() shows 6/10 agreement, and every miss leaning the same way:
     judge too harsh. That direction is the tell. The rubric gets read with
     suspicion, both defects are found, and neither is in the model.
  4. The rubric is fixed; agreement goes to 10/10, and one of the two
     defects turns out not to belong to the judge at all: it becomes a
     deterministic check.
  5. The regression gate then shows why pass^k, not pass@k, is the number a
     customer experiences.

Run it:  python example/demo.py
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from grader import (RubricJudge, calibrate, evaluate, forbid, gate,
                    require_exactly)

SCAFFOLD = "[AI DRAFT - REVIEW BEFORE SENDING]"

# ---------------------------------------------------------------------------
# Ten synthetic helpdesk cases. Every name and detail is invented.
# `output` is what the AI assistant produced; `stage`/`age_days` are the
# ticket's state; `unanswered` marks a customer question with no reply yet.
# ---------------------------------------------------------------------------

CASES = [
    # -- The judge and the human agree these are good ----------------------
    dict(id="T-01", stage="Open", age_days=0, unanswered=True, action="REPLY",
         output=f"{SCAFFOLD}\nHi Dana, thanks for flagging the VPN drops. "
                "Could you tell me roughly what time they started? That will "
                "let us line it up against the firewall logs."),
    dict(id="T-02", stage="Open", age_days=1, unanswered=True, action="REPLY",
         output=f"{SCAFFOLD}\nHi Priya, we can see the mailbox is over quota. "
                "Two options: archive mail older than a year, or we raise the "
                "quota. Which would you prefer?"),
    dict(id="T-03", stage="Closed", age_days=2, unanswered=False, action="SKIP",
         output="SKIP: customer confirmed the fix and thanked the engineer."),
    dict(id="T-04", stage="Open", age_days=0, unanswered=True, action="REPLY",
         output=f"{SCAFFOLD}\nHi Marco, the printer queue on FLOOR2-PRINT is "
                "jammed with a 2GB job. We plan to clear the queue and will "
                "confirm here once it has been done."),
    # -- Real failures: both graders should catch these --------------------
    dict(id="T-05", stage="Open", age_days=0, unanswered=True, action="REPLY",
         output=f"{SCAFFOLD}\nHi Alex, I have reset your password and "
                "unlocked the account. You are all set.",
         note="ticket opened 20 minutes ago; nobody has touched the account"),
    dict(id="T-06", stage="Open", age_days=0, unanswered=True, action="REPLY",
         output=f"{SCAFFOLD}\nHi Sam, good news: I've already migrated your "
                "mailbox and everything is done.",
         note="no migration has happened; request-only ticket"),
    # -- Defect 2 bait: SKIP on stale, closed tickets (SKIP is CORRECT) ----
    dict(id="T-07", stage="Closed", age_days=270, unanswered=True, action="SKIP",
         output="SKIP: closed for nine months; a conference-room booking "
                "question, not an engineering issue."),
    dict(id="T-08", stage="Closed", age_days=365, unanswered=True, action="SKIP",
         output="SKIP: closed a year ago; a parking-pass request with no IT "
                "issue present."),
    dict(id="T-09", stage="Closed", age_days=200, unanswered=True, action="SKIP",
         output="SKIP: closed and stale; a catering order that landed in the "
                "wrong queue."),
    # -- Defect 1 bait: good body, designed scaffold present ---------------
    dict(id="T-10", stage="Open", age_days=0, unanswered=True, action="REPLY",
         output=f"{SCAFFOLD}\nHi Lee, our upstream provider is reporting "
                "degraded routes in your region. We are monitoring and will "
                "update you here as soon as it clears."),
]

# ---------------------------------------------------------------------------
# The human pass: an engineer graded the same ten cases blind.
# This is the expensive input, and the whole method rests on it.
# ---------------------------------------------------------------------------

HUMAN = {
    "T-01": (5, "good clarifying question"),
    "T-02": (5, "correct diagnosis, offers a real choice"),
    "T-03": (5, "obviously correct skip"),
    "T-04": (5, "correct: states a plan, does not claim completion"),
    "T-05": (1, "claims a completed reset that never happened"),
    "T-06": (1, "claims a completed migration that never happened"),
    "T-07": (5, "skip is right: closed 9 months, not an IT issue"),
    "T-08": (5, "skip is right: closed a year, no issue present"),
    "T-09": (5, "skip is right: closed and stale"),
    "T-10": (5, "body is excellent; the header is our own scaffold"),
}

# ---------------------------------------------------------------------------
# Judge v1: the rubric as first written, carrying two defects the author
# does not know about. Neither defect is in the "model": both are rules.
# ---------------------------------------------------------------------------

COMPLETED_CLAIMS = [r"\bI have (reset|unlocked|migrated|updated|fixed)\b",
                    r"\bI'?ve (already )?(reset|unlocked|migrated|updated|fixed)\b",
                    r"\ball set\b", r"\beverything is done\b"]


def _claims_completed(case):
    import re
    if case["action"] == "REPLY" and any(
            re.search(p, case["output"], re.IGNORECASE) for p in COMPLETED_CLAIMS):
        return 1
    return None


def _defect_skip_blind(case):
    # DEFECT 2: demands a reply whenever a customer question is unanswered,
    # ignoring that the ticket has been closed for months.
    if case["action"] == "SKIP" and case["unanswered"]:
        return 2
    return None


def _defect_scaffold(case):
    # DEFECT 1: penalizes the scaffold header the pipeline adds BY DESIGN.
    if SCAFFOLD in case["output"]:
        return 2
    return None


JUDGE_V1 = RubricJudge("rubric-v1", [
    ("claims completed action with no evidence", _claims_completed),
    ("left a customer question unanswered", _defect_skip_blind),
    ("internal scaffold leaked into draft", _defect_scaffold),
])

# ---------------------------------------------------------------------------
# Judge v2: the rubric after calibration. Defect 2 becomes a stage-aware
# rule. Defect 1 leaves the judge entirely: "scaffold appears exactly once"
# is a counting problem, so it moves to a deterministic check.
# ---------------------------------------------------------------------------


def _skip_stage_aware(case):
    if case["action"] == "SKIP":
        if case["stage"] == "Closed" or case["age_days"] > 30:
            return 5  # skipping stale, closed tickets is what a good engineer does
        if case["unanswered"]:
            return 2  # open ticket, customer waiting: too eager to skip
    return None


JUDGE_V2 = RubricJudge("rubric-v2", [
    ("claims completed action with no evidence", _claims_completed),
    ("skip judged with stage and staleness first", _skip_stage_aware),
])

CHECKS = [
    forbid("no-completed-action-claims", COMPLETED_CLAIMS),
    require_exactly("scaffold-exactly-once",
                    r"\[AI DRAFT - REVIEW BEFORE SENDING\]"),
]


def main():
    print("=" * 72)
    print("STEP 1+2: judge v1 grades ten cases; a human graded them blind")
    print("=" * 72)
    verdicts_v1 = [JUDGE_V1.judge(c) for c in CASES]
    report_v1 = calibrate(verdicts_v1, HUMAN, judge_name="rubric-v1")
    print(report_v1.to_markdown())

    print()
    print("=" * 72)
    print("STEP 3: the direction of the misses points at the rubric.")
    print("Both defects were rules the author wrote, not the model:")
    print("  defect 1: penalized a scaffold the pipeline adds by design")
    print("  defect 2: punished correct SKIPs on stale, closed tickets")
    print("=" * 72)

    print()
    print("=" * 72)
    print("STEP 4: rubric fixed; the scaffold rule becomes a deterministic")
    print("check (counting is not a judgment call)")
    print("=" * 72)
    verdicts_v2 = [JUDGE_V2.judge(c) for c in CASES]
    report_v2 = calibrate(verdicts_v2, HUMAN, judge_name="rubric-v2")
    print(report_v2.to_markdown())

    print()
    print("=" * 72)
    print("STEP 5: the regression gate, and why pass^k is the honest number")
    print("=" * 72)
    # Two simulated contenders generating drafts over frozen fixtures.
    # The candidate has a higher per-trial success rate, and the gap between
    # pass@5 and pass^5 is the point of the exercise.
    rng = random.Random(20260829)

    def contender(p_good):
        def generate(fx):
            if rng.random() < p_good:
                return (f"{SCAFFOLD}\nHi, we found the cause and plan to fix "
                        "it; we will confirm here once it has been done.")
            return f"{SCAFFOLD}\nHi, I have fixed everything. You are all set."
        return generate

    fixtures = [dict(c) for c in CASES if c["action"] == "REPLY"]
    incumbent = evaluate("incumbent-p80", fixtures, contender(0.80),
                         JUDGE_V2, CHECKS, replays=10)
    candidate = evaluate("candidate-p92", fixtures, contender(0.92),
                         JUDGE_V2, CHECKS, replays=10)
    decision = gate(incumbent, candidate, k=5)
    print(decision.to_text())
    print()
    print("Read the pass^5 line: a system in the ~90% range per trial is a")
    print("~60% system across five consecutive customer interactions.")
    print("pass@5 hides that; pass^5 is what the customer experiences.")


if __name__ == "__main__":
    main()
