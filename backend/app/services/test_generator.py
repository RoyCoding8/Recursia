"""Test generator — LLM-powered independent test case generation.

Follows AgentCoder separation: test generator is independent from code author
to prevent self-serving bias in verification. Supports both stdin/stdout tests
and script-based validation for different task types.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.adapters.llm_client import LLMClient, LLMGenerateRequest, LLMMessage
from app.sandbox.executor import TestCase
from app.services.persona_registry import PersonaProfile

SYSTEM = (
    "You are an independent test designer for software verification.\n"
    "Given an objective and optionally code, generate comprehensive test cases as JSON.\n\n"
    "For CLI programs/scripts, return stdin/stdout tests:\n"
    '{"test_cases": [{"name": "...", "input": "stdin", "expected": "stdout", '
    '"category": "basic|edge|stress|corner"}], "coverage_notes": "..."}\n\n'
    "For libraries/APIs/complex tasks, return a self-contained test script:\n"
    '{"test_script": {"language": "python", "code": "...assert statements...'
    '\\nprint(\'PASS\')", "expected_stdout": "PASS"}, "coverage_notes": "..."}\n\n'
    "Rules:\n"
    "- Generate at least 5 test cases OR a thorough test script\n"
    "- Cover: basic, edge, boundary, error handling, and stress scenarios\n"
    "- Do NOT bias toward any particular implementation\n"
    "- Match format to task type"
)


@dataclass(slots=True, frozen=True)
class ScriptTest:
    language: str
    code: str
    expected_stdout: str = "PASS"


@dataclass(slots=True, frozen=True)
class GeneratedTests:
    cases: list[TestCase]
    script: ScriptTest | None = None
    coverage_notes: str = ""


class TestGeneratorService:
    def __init__(self, llm: LLMClient, *, temperature: float = 0.3,
                 persona: PersonaProfile | None = None):
        self._llm = llm
        self._temp = temperature
        self._persona = persona

    def generate(self, objective: str, *, code: str | None = None,
                 hints: list[str] | None = None) -> list[TestCase]:
        result = self.generate_full(objective, code=code, hints=hints)
        return result.cases

    def generate_full(self, objective: str, *, code: str | None = None,
                      hints: list[str] | None = None) -> GeneratedTests:
        sys = self._persona.system_prompt if self._persona else SYSTEM
        ctx = f"Objective: {objective}"
        if code:
            ctx += f"\n\nCode (for reference only — generate INDEPENDENT tests):\n```\n{code}\n```"
        if hints:
            ctx += f"\n\nTest hints: {json.dumps(hints)}"
        req = LLMGenerateRequest(
            messages=[
                LLMMessage(role="system", content=sys),
                LLMMessage(role="user", content=ctx),
            ],
            temperature=self._temp,
            metadata={"service": "test_generator"},
        )
        raw = self._llm.generate_json(req)
        return self._parse(raw)

    @staticmethod
    def _parse(raw: Any) -> GeneratedTests:
        if not isinstance(raw, (dict, list)):
            return GeneratedTests(cases=[])

        # Handle list format (just test cases)
        if isinstance(raw, list):
            return GeneratedTests(cases=_parse_cases(raw))

        notes = raw.get("coverage_notes", "")

        # Script-based test format
        script_raw = raw.get("test_script")
        if isinstance(script_raw, dict) and script_raw.get("code"):
            script = ScriptTest(
                language=str(script_raw.get("language", "python")),
                code=str(script_raw["code"]),
                expected_stdout=str(script_raw.get("expected_stdout", "PASS")),
            )
            # Convert script to a single TestCase for sandbox compatibility
            cases = [TestCase(
                name="script_test",
                input="",
                expected=script.expected_stdout,
            )]
            return GeneratedTests(cases=cases, script=script, coverage_notes=notes)

        # stdin/stdout test cases
        cases = _parse_cases(raw.get("test_cases", []))
        return GeneratedTests(cases=cases, coverage_notes=notes)


def _parse_cases(raw_cases: Any) -> list[TestCase]:
    if not isinstance(raw_cases, list):
        return []
    result = []
    for c in raw_cases:
        if not isinstance(c, dict):
            continue
        result.append(TestCase(
            name=c.get("name", f"test_{len(result)}"),
            input=str(c.get("input", "")),
            expected=str(c.get("expected", "")),
            timeout_s=float(c.get("timeout_s", 10.0)),
        ))
    return result


__all__ = ["GeneratedTests", "ScriptTest", "TestGeneratorService"]
