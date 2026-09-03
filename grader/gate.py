"""The regression gate: a candidate must beat the incumbent, and ties lose.

This is the piece that turns evals from a dashboard into a control. A prompt
change ships only if it wins outright on frozen fixtures. Three decisions in
here carried the most weight in production:

1.  **Replay every fixture several times.** The judge is a noisy instrument.
    One replay per fixture measures the judge's mood; several replays measure
    the prompt. Noise damping comes from replication, not from temperature=0
    (which current frontier models reject on structured output anyway).

2.  **Report pass^k, not just pass@k.** pass@k answers "could it ever do
    this?" and rises toward 1.0 with retries. pass^k answers "does it do
    this every time?" and falls with repetition. For anything a customer
    sees, pass^k is the honest number: a 90%-per-trial system is a 59%
    system across five consecutive interactions. Optimizing pass@k while a
    customer experiences pass^k is how a dashboard stays green while trust
    erodes.

3.  **A winner is published switched OFF.** The gate's output is a
    recommendation; a human flips the switch, and undoing the flip needs no
    deployment. The automation cannot detect that its own measuring stick is
    bent (see calibrate.py), so nothing the loop produces ships itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .checks import all_passed, run_checks


@dataclass
class FixtureResult:
    fixture_id: str
    scores: list = field(default_factory=list)       # judge score per replay
    check_failures: list = field(default_factory=list)  # names, all replays
    passes: int = 0
    replays: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passes / self.replays if self.replays else 0.0


@dataclass
class RunResult:
    name: str
    fixtures: list = field(default_factory=list)

    @property
    def mean_score(self) -> float:
        scores = [s for f in self.fixtures for s in f.scores]
        return sum(scores) / len(scores) if scores else 0.0

    @property
    def per_trial_pass_rate(self) -> float:
        total = sum(f.replays for f in self.fixtures)
        passes = sum(f.passes for f in self.fixtures)
        return passes / total if total else 0.0

    def pass_at_k(self, k: int) -> float:
        """P(at least one success in k independent trials)."""
        p = self.per_trial_pass_rate
        return 1.0 - (1.0 - p) ** k

    def pass_pow_k(self, k: int) -> float:
        """P(success in ALL of k consecutive trials): the customer's number."""
        return self.per_trial_pass_rate ** k


def evaluate(name, fixtures, generate, judge, checks, replays: int = 3,
             pass_threshold: int = 4) -> RunResult:
    """Run one contender (a prompt, a model, a config) over frozen fixtures.

    generate: fn(fixture) -> output text. The system under test.
    judge / checks: the graders. A replay passes only when the judge scores
    at or above threshold AND every deterministic check passes: a charming
    answer that fails a check is a failure, and a rule-abiding answer the
    judge hates is also a failure. Both graders must be satisfied because
    they catch disjoint defects.
    """
    run = RunResult(name=name)
    for fx in fixtures:
        fr = FixtureResult(fixture_id=fx["id"])
        for _ in range(replays):
            output = generate(fx)
            verdict = judge.judge({**fx, "output": output})
            results = run_checks(checks, fx, output)
            fr.replays += 1
            fr.scores.append(verdict.score)
            failed = [r.name for r in results if not r.passed]
            fr.check_failures.extend(failed)
            if verdict.score >= pass_threshold and not failed:
                fr.passes += 1
        run.fixtures.append(fr)
    return run


@dataclass
class GateDecision:
    promote: bool
    reason: str
    incumbent: RunResult
    candidate: RunResult
    k: int

    def to_text(self) -> str:
        i, c = self.incumbent, self.candidate
        lines = [
            f"gate: candidate '{c.name}' vs incumbent '{i.name}' (k={self.k})",
            f"  mean score    incumbent {i.mean_score:.2f}   candidate {c.mean_score:.2f}",
            f"  per-trial     incumbent {i.per_trial_pass_rate:.2f}   candidate {c.per_trial_pass_rate:.2f}",
            f"  pass@{self.k}        incumbent {i.pass_at_k(self.k):.3f}  candidate {c.pass_at_k(self.k):.3f}",
            f"  pass^{self.k}        incumbent {i.pass_pow_k(self.k):.3f}  candidate {c.pass_pow_k(self.k):.3f}",
            f"  decision: {'PROMOTE (publish switched off; a human flips it on)' if self.promote else 'REJECT'}",
            f"  reason: {self.reason}",
        ]
        return "\n".join(lines)


def gate(incumbent: RunResult, candidate: RunResult, k: int = 5) -> GateDecision:
    """Candidate must beat the incumbent outright on BOTH mean and pass^k.

    Ties lose, deliberately. The incumbent has production history behind it
    and the candidate has a judge's opinion; equality is not evidence of
    improvement, and churn has a cost all its own.
    """
    better_mean = candidate.mean_score > incumbent.mean_score
    better_consistency = candidate.pass_pow_k(k) > incumbent.pass_pow_k(k)
    if better_mean and better_consistency:
        return GateDecision(True, "beats incumbent on mean and on pass^k",
                            incumbent, candidate, k)
    if better_mean:
        return GateDecision(False, "higher mean but not more consistent: "
                            "pass^k did not improve", incumbent, candidate, k)
    if better_consistency:
        return GateDecision(False, "more consistent but lower mean",
                            incumbent, candidate, k)
    return GateDecision(False, "no improvement on either axis",
                        incumbent, candidate, k)
