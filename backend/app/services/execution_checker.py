"""Execution-based checker client — verifies code by running it against generated tests.

Combines LLM-based test generation with sandbox execution to verify code outputs.
Falls back to LLM-only checking when code is not extractable or sandbox is unavailable.
"""
from __future__ import annotations

from typing import Any

from app.adapters.llm_client import LLMClient
from app.sandbox.executor import SandboxExecutor, SuiteResult, create_sandbox, detect_language
from app.services.checker import CheckerRequest, LLMCheckerClient
from app.services.test_generator import TestGeneratorService


class ExecutionCheckerClient:
    """Checker that runs code against auto-generated test cases.

    Pipeline:
    1. Extract code from output
    2. Generate test cases for the objective (independent of code)
    3. Run code against tests in sandbox (stdin/stdout or script-based)
    4. If execution fails or no code found, fall back to LLM checker
    """

    def __init__(self, *, sandbox: SandboxExecutor, test_gen: TestGeneratorService,
                 llm_fallback: LLMCheckerClient):
        self._sandbox = sandbox
        self._test_gen = test_gen
        self._fallback = llm_fallback

    def evaluate(self, request: CheckerRequest) -> object:
        code = _extract_code(request.output)
        if not code:
            return self._fallback.evaluate(request)

        hints = _extract_test_hints(request.output)
        gen = self._test_gen.generate_full(request.objective, code=code, hints=hints)
        if not gen.cases:
            return self._fallback.evaluate(request)

        # Script-based test: run the test script itself (which imports/validates the code)
        if gen.script:
            combined = code + "\n\n" + gen.script.code
            result = self._sandbox.run_code(combined, language=gen.script.language)
            actual = result.stdout.rstrip()
            expected = gen.script.expected_stdout.rstrip()
            if result.ok and actual == expected:
                return {"verdict": "pass", "reason": "Script-based verification passed.",
                        "suggested_fix": "", "confidence": 0.85, "violations": []}
            error = result.stderr[:300] if result.stderr else f"expected {expected!r}, got {actual!r}"
            return {"verdict": "fail", "reason": f"Script-based verification failed: {error}",
                    "suggested_fix": error, "confidence": 0.85,
                    "violations": [f"script_test: {error}"]}

        # stdin/stdout tests
        lang = _extract_language(request.output, code)
        suite = self._sandbox.run_tests(code, gen.cases, language=lang)
        return _suite_to_checker_result(suite)


def _extract_code(output: Any) -> str | None:
    """Try to extract runnable code from worker output."""
    if not isinstance(output, dict):
        return None
    # Direct code field
    if "code" in output and isinstance(output["code"], str) and output["code"].strip():
        return output["code"].strip()
    # From step_results → output → code
    for step in output.get("step_results", []):
        if not isinstance(step, dict):
            continue
        step_out = step.get("output", {})
        if isinstance(step_out, dict) and "code" in step_out:
            return str(step_out["code"]).strip()
    # From file_proposals
    for fp in output.get("file_proposals", []):
        if isinstance(fp, dict) and fp.get("content"):
            return str(fp["content"]).strip()
    return None


def _extract_language(output: Any, code: str) -> str | None:
    """Try to extract language hint from output, fallback to detection."""
    if isinstance(output, dict):
        lang = output.get("language")
        if lang and isinstance(lang, str):
            return lang.lower()
        for step in output.get("step_results", []):
            if isinstance(step, dict):
                step_out = step.get("output", {})
                if isinstance(step_out, dict) and "language" in step_out:
                    return str(step_out["language"]).lower()
    return detect_language(code).value


def _extract_test_hints(output: Any) -> list[str]:
    """Extract test hints from worker output if present."""
    if not isinstance(output, dict):
        return []
    hints = output.get("test_hints")
    if isinstance(hints, list) and hints:
        return [str(h) for h in hints if h]
    for step in output.get("step_results", []):
        if isinstance(step, dict):
            step_out = step.get("output", {})
            if isinstance(step_out, dict) and "test_hints" in step_out:
                h = step_out["test_hints"]
                return [str(x) for x in h if x] if isinstance(h, list) else []
    return []


def _suite_to_checker_result(suite: SuiteResult) -> dict[str, Any]:
    """Convert SuiteResult to checker-compatible JSON payload."""
    if suite.compile_error:
        return {
            "verdict": "fail",
            "reason": f"Compilation failed: {suite.compile_error[:300]}",
            "suggested_fix": "Fix compilation errors before verification.",
            "confidence": 1.0,
            "violations": [f"compile_error: {suite.compile_error[:200]}"],
        }

    if suite.all_passed:
        return {
            "verdict": "pass",
            "reason": f"All {suite.total} tests passed.",
            "suggested_fix": "",
            "confidence": min(0.5 + (suite.total * 0.1), 0.95),
            "violations": [],
        }

    failures = [r for r in suite.results if not r.passed]
    violations = []
    for f in failures[:5]:
        detail = f.error or "wrong answer"
        violations.append(
            f"{f.name}: expected={f.expected!r}, got={f.actual!r} ({detail})"
        )

    first_fail = failures[0]
    fix_hint = ""
    if first_fail.error == "wrong answer":
        fix_hint = (f"Test '{first_fail.name}' expected {first_fail.expected!r} "
                    f"but got {first_fail.actual!r}. Check logic for this case.")
    elif first_fail.exec_result.timed_out:
        fix_hint = f"Test '{first_fail.name}' timed out. Optimize time complexity."
    elif first_fail.exec_result.stderr:
        fix_hint = f"Runtime error in '{first_fail.name}': {first_fail.exec_result.stderr[:200]}"

    return {
        "verdict": "fail",
        "reason": f"{suite.failed}/{suite.total} tests failed.",
        "suggested_fix": fix_hint,
        "confidence": min(0.5 + (suite.total * 0.1), 0.95),
        "violations": violations,
    }


def build_execution_checker(
    llm: LLMClient,
    *,
    sandbox_timeout_s: float = 30.0,
    test_gen_temperature: float = 0.3,
) -> ExecutionCheckerClient:
    """Factory: build execution checker with all dependencies."""
    sandbox = create_sandbox(timeout_s=sandbox_timeout_s)
    test_gen = TestGeneratorService(llm, temperature=test_gen_temperature)
    fallback = LLMCheckerClient(llm)
    return ExecutionCheckerClient(sandbox=sandbox, test_gen=test_gen, llm_fallback=fallback)


__all__ = ["ExecutionCheckerClient", "build_execution_checker"]
