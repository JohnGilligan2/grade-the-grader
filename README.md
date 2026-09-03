# grade-the-grader

A small harness for LLM-as-judge evaluation that treats the judge as an
unmeasured instrument until it has been calibrated against a human.

Four copy-in files, stdlib only. The example and tests run offline in
seconds; no API key. An Anthropic-backed judge is included for live use.

**Measured on the production system this was extracted from: the judge
matched human review on 6 of 10 verdicts, and every single miss was the
judge grading good work too low. Both root causes were in the grading
rubric the author wrote, not in the judge model.** One of those phantom
defects had already leaked into two proposed prompt revisions before the
calibration pass caught it.

The longer story is written up in
[Grading the Grader](https://johngilligan2.github.io/2026/08/26/grading-the-grader.html).
This repo is the methodology as runnable code.

## The problem

The standard eval stack is: golden set, LLM judge scoring against a rubric,
regression gate before any prompt change ships, maybe an optimizer drafting
revisions from the judge's critiques. Tidy loop. One question usually goes
unasked: who grades the grader?

A judge that is too generous is dangerous in an obvious way. A judge that is
too harsh is dangerous in a quieter one. In a self-improving loop, the
system does not optimize toward good; it optimizes toward whatever the judge
rewards. A miscalibrated judge does not produce a noisy system. It produces
a confident, efficient system marching in the wrong direction, with graphs
that look great.

## The method

1. **Hand-check a sample.** Take the judge's verdicts on real cases and
   grade the same cases yourself, blind. Ten cases were enough in practice.
   An afternoon of work; the defects it caught had survived weeks of the
   system "working".

2. **Read the direction of the misses, not just the rate.** Random misses
   scatter both ways. Misses that all lean one way are a systematic defect,
   and in every instance so far the defect was in the rubric, not the model.
   `calibrate()` computes this and says so explicitly.

3. **Grade the disagreements, not the agreements.** Every mismatch gets read
   in full. The agreement rate is a summary; the disagreement table is the
   diagnostic.

4. **Demote settled questions into deterministic checks.** One production
   rubric defect was the judge coin-flipping over a scaffold header the
   pipeline adds by design. "Appears exactly once" is a counting problem;
   it moved out of the judge into a regex, and the judge was told to ignore
   the scaffold entirely. Any judge critique that recurs verbatim is a
   candidate for the same demotion.

5. **Gate on pass^k, and let a human flip the switch.** A candidate prompt
   must beat the incumbent outright on frozen fixtures, replayed several
   times to damp judge noise; ties lose. The winner is published switched
   off, so approving it is one click and rolling it back needs no deploy.
   The automation cannot detect that its own measuring stick is bent, so
   nothing the loop produces ships itself.

## pass@k is not the number your customer experiences

pass@k answers "could it ever do this?" and rises toward 1.0 with retries.
pass^k answers "does it do this every time?" and falls with repetition. At a
90% per-trial pass rate:

```
pass@5 = 1 - (1-0.9)^5 = 0.99999
pass^5 = 0.9^5         = 0.59
```

A 90% system is a 59% system across five consecutive customer interactions.
Both numbers are printed by the gate; only one of them belongs on a
customer-facing dashboard.

## The pieces

| file | what it does |
|---|---|
| [`grader/judge.py`](grader/judge.py) | Structured verdicts with an escape hatch: `insufficient_information` is a legal answer, because a judge forced to score everything will confidently score what it cannot see. `RubricJudge` (deterministic, offline) and `AnthropicJudge` (schema-enforced API judge). |
| [`grader/calibrate.py`](grader/calibrate.py) | Judge-vs-human agreement, the disagreement table, and direction-of-miss analysis. Three or more misses all leaning one way triggers the "suspect the rubric" verdict. |
| [`grader/checks.py`](grader/checks.py) | Deterministic rules that run alongside the judge: forbidden claims, required-exactly-once scaffolds, length bounds. Also the anti-gaming layer: a candidate that learns to please the judge still has to pass rules that cannot be charmed. |
| [`grader/gate.py`](grader/gate.py) | The regression gate: N replays per fixture, judge AND checks must both pass, pass@k and pass^k reported side by side, candidate beats incumbent or loses. |

## Quick start

```bash
python example/demo.py     # the whole method on synthetic data, offline
python tests/test_grader.py
```

The demo recreates the production sequence with fake tickets: a judge with
two planted rubric defects grades ten cases, a blind human pass disagrees,
every miss leans judge-too-harsh, the report says to suspect the rubric,
both defects get fixed (one by rewriting a rule, one by demoting it to a
deterministic check), agreement goes to 10/10, and the gate then shows a
candidate promoted on mean score and pass^5 together.

## Using it on your own system

```python
from grader import AnthropicJudge, calibrate, evaluate, forbid, gate

judge = AnthropicJudge(system_rubric=MY_RUBRIC)

# 1. calibrate before trusting a single score
verdicts = [judge.judge(c) for c in sample_cases]
report = calibrate(verdicts, my_human_verdicts, judge_name="prod-judge")
print(report.to_markdown())          # read every disagreement in full

# 2. only then let the judge gate anything
checks = [forbid("no-completed-claims", [r"\bI have (fixed|reset)\b"])]
incumbent = evaluate("prompt-v7", fixtures, gen_v7, judge, checks, replays=5)
candidate = evaluate("prompt-v8", fixtures, gen_v8, judge, checks, replays=5)
print(gate(incumbent, candidate, k=5).to_text())
```

Two rules that transfer regardless of stack:

- **Grade the traced seam, nothing else.** Whatever text you hand the judge
  is what gets graded. Chrome your code adds around the model's output
  (badges, banners, review scaffolds) must stay outside the graded seam, or
  the judge scores markup the model never wrote and every downstream
  consumer inherits the error.
- **Recalibrate after every rubric change, and quote the current number.**
  The agreement rate you measured before the fix is not the agreement rate
  you have.

## What this is not

- Not a benchmark and not a dataset. The golden set is yours: mined from
  your own operational history, pairing what the model said with what your
  people actually did next.
- Not a replacement for reading transcripts. Every number this produces was
  designed to point you at specific cases to read, not to spare you the
  reading.
- Calibration here covers transcript quality. Outcome evaluation (the agent
  said it did X; did X happen in the real system?) is a separate layer this
  library does not provide.

## License

MIT.
