"""Calibration: measure the judge against a human before trusting its scores.

A judge score is a claim, not a measurement, until it has been checked against
a person. This module does the checking, and one specific piece of analysis
that turned out to matter more than the agreement rate itself:

    the DIRECTION of the disagreements.

Random misses scatter in both directions. Misses that all point the same way
(judge always harsher than the human, or always more lenient) are not noise,
they are a systematic defect, and in every case we have hit so far the defect
was in the rubric we wrote, not in the judge model. A judge that is
systematically harsh feeds "problems" to whatever consumes its critiques,
and an optimizer downstream will faithfully fix what was never broken.

Grade the disagreements, not the agreements. Ten cases were enough to expose
two rubric defects in production, because every mismatch was read in full
instead of averaged away.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Disagreement:
    case_id: str
    judge_score: int
    human_score: int
    judge_reason: str
    human_note: str

    @property
    def direction(self) -> str:
        return "judge_harsh" if self.judge_score < self.human_score else "judge_lenient"


@dataclass
class CalibrationReport:
    judge_name: str
    n: int
    agreements: int
    disagreements: list = field(default_factory=list)
    skipped: int = 0  # verdicts the judge declined (insufficient_information)

    @property
    def agreement_rate(self) -> float:
        return self.agreements / self.n if self.n else 0.0

    @property
    def harsh(self) -> int:
        return sum(1 for d in self.disagreements if d.direction == "judge_harsh")

    @property
    def lenient(self) -> int:
        return sum(1 for d in self.disagreements if d.direction == "judge_lenient")

    @property
    def systematic(self) -> bool:
        """All misses in one direction, and enough of them to mean something.

        Three or more disagreements that all lean the same way is a rubric
        smell, not judge noise. Two can be coincidence.
        """
        misses = len(self.disagreements)
        return misses >= 3 and (self.harsh == misses or self.lenient == misses)

    def verdict_line(self) -> str:
        if not self.disagreements:
            return "Judge and human agree on every graded case."
        line = (f"Agreement {self.agreements}/{self.n}. "
                f"{self.harsh} miss(es) judge-too-harsh, "
                f"{self.lenient} judge-too-lenient.")
        if self.systematic:
            line += (" ALL misses lean one way: suspect the RUBRIC, not the"
                     " model. Read every disagreement below in full before"
                     " trusting another score from this judge.")
        return line

    def to_markdown(self) -> str:
        rows = [
            f"# Judge calibration: {self.judge_name}",
            "",
            self.verdict_line(),
            "",
            "| case | judge | human | direction | judge said | human said |",
            "|---|---|---|---|---|---|",
        ]
        for d in self.disagreements:
            rows.append(
                f"| {d.case_id} | {d.judge_score} | {d.human_score} "
                f"| {d.direction} | {d.judge_reason[:80]} | {d.human_note[:80]} |"
            )
        if self.skipped:
            rows += ["", f"{self.skipped} case(s) skipped: judge returned"
                         " insufficient_information. Skipping is correct"
                         " behavior; forcing a score would be the bug."]
        return "\n".join(rows)


def calibrate(judge_verdicts, human_verdicts, tolerance: int = 1,
              judge_name: str = "judge") -> CalibrationReport:
    """Compare judge verdicts with human verdicts over the same cases.

    human_verdicts: dict of case_id -> (score, note). The human pass is the
    expensive input here, which is the point: it took an afternoon, and the
    defects it caught had survived weeks of the system "working".

    tolerance: scores within this distance count as agreement. Default 1,
    because a 4 vs a 5 is taste and a 2 vs a 5 is a defect.

    Cases where the judge declined (insufficient_information) are counted
    separately, not as disagreements: an honest "I can't grade this" is the
    escape hatch working as designed.
    """
    report = CalibrationReport(judge_name=judge_name, n=0, agreements=0)
    for v in judge_verdicts:
        if v.case_id not in human_verdicts:
            continue
        if not v.usable():
            report.skipped += 1
            continue
        human_score, human_note = human_verdicts[v.case_id]
        report.n += 1
        if abs(v.score - human_score) <= tolerance:
            report.agreements += 1
        else:
            report.disagreements.append(Disagreement(
                case_id=v.case_id,
                judge_score=v.score,
                human_score=human_score,
                judge_reason=v.reason,
                human_note=human_note,
            ))
    return report
