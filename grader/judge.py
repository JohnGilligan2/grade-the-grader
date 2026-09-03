"""The judge half of the harness: a structured LLM grader with an escape hatch.

Two design rules, both learned in production:

1.  Verdicts are schema-enforced, never parsed out of prose. A judge that
    free-texts its score will eventually free-text something unparseable,
    and the failure will be silent.

2.  The schema carries an explicit escape hatch (`insufficient_information`).
    A judge forced to score every case will confidently score the cases it
    cannot see well enough to grade, and those verdicts poison every
    downstream consumer. "Unknown" must be a legal answer.

There is no temperature knob here. Recent frontier models reject
temperature/top_p on structured output; determinism comes from the enum'd
schema and, where it matters, from majority vote across replays (see gate.py),
not from temperature=0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# One verdict shape for every judge, human or model.
VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "reason": {"type": "string"},
        "insufficient_information": {"type": "boolean"},
    },
    "required": ["score", "reason", "insufficient_information"],
}


@dataclass(frozen=True)
class Verdict:
    case_id: str
    score: int          # 1 (bad) .. 5 (good)
    reason: str
    insufficient: bool = False

    def usable(self) -> bool:
        return not self.insufficient


def render_case(case: dict, clip: int = 2500) -> str:
    """Render a case dict into the text a judge grades.

    Field order is stable and values are clipped, so two runs over the same
    case produce byte-identical judge input. Whatever you feed the judge is
    what the judge grades: keep code-added chrome (badges, banners, scaffold
    the pipeline injects) OUT of the rendered seam, or the judge will grade
    markup the model never wrote.
    """
    lines = []
    for key in sorted(case):
        if key == "id":
            continue
        value = case[key]
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=True, sort_keys=True)
        lines.append(f"{key.upper()}: {str(value)[:clip]}")
    return "\n".join(lines)


class Judge:
    """Interface. A judge maps a case to a Verdict. Nothing more."""

    name = "judge"

    def judge(self, case: dict) -> Verdict:  # pragma: no cover - interface
        raise NotImplementedError


class RubricJudge(Judge):
    """A deterministic judge built from explicit rules.

    Used two ways: as the offline stand-in for an LLM judge in demos and
    tests, and as a reminder that a rubric IS code. Every rule below is a
    claim about quality that can be wrong, and calibrate.py exists to catch
    exactly that.

    rules: list of (name, fn) where fn(case) -> int score 1-5, or None to
    pass to the next rule. First rule to return a score wins.
    """

    def __init__(self, name: str, rules, default: int = 5):
        self.name = name
        self.rules = rules
        self.default = default

    def judge(self, case: dict) -> Verdict:
        for rule_name, fn in self.rules:
            score = fn(case)
            if score is not None:
                return Verdict(case["id"], score, rule_name)
        return Verdict(case["id"], self.default, "no rule fired")


class AnthropicJudge(Judge):
    """The production judge: schema-enforced structured output.

    Requires the `anthropic` package and ANTHROPIC_API_KEY. Imported lazily so
    the rest of this repo runs offline.
    """

    def __init__(self, system_rubric: str, model: str = "claude-opus-4-8",
                 max_tokens: int = 1000):
        import anthropic  # deferred: offline users never pay this import

        self.name = f"anthropic:{model}"
        self.model = model
        self.max_tokens = max_tokens
        self.system_rubric = system_rubric
        self._client = anthropic.Anthropic()

    def judge(self, case: dict) -> Verdict:
        message = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # cache_control: the rubric is identical across every case in a
            # run, so it should be written to cache once, not paid per case.
            system=[{"type": "text", "text": self.system_rubric,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": render_case(case)}],
            output_config={"format": {"type": "json_schema",
                                      "schema": VERDICT_SCHEMA}},
        )
        text = next(b.text for b in message.content if b.type == "text")
        v = json.loads(text)
        return Verdict(case["id"], v["score"], v["reason"],
                       v["insufficient_information"])
