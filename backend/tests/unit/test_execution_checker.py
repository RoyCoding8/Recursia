"""Tests for execution-based checker and test generator integration."""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.sandbox.executor import (
    ExecResult, SandboxExecutor, SuiteResult, TestCase, TestResult,
)
from app.services.checker import CheckerRequest, CheckerScope, LLMCheckerClient
from app.services.execution_checker import (
    ExecutionCheckerClient, _extract_code, _extract_language,
    _extract_test_hints, _suite_to_checker_result,
)
from app.services.test_generator import GeneratedTests, ScriptTest, TestGeneratorService


class TestExtractCode:
    def test_direct_code_field(self):
        assert _extract_code({"code": "print('hi')"}) == "print('hi')"

    def test_from_step_results(self):
        output = {"step_results": [{"output": {"code": "x = 1"}}]}
        assert _extract_code(output) == "x = 1"

    def test_from_file_proposals(self):
        output = {"file_proposals": [{"path": "main.py", "content": "y = 2"}]}
        assert _extract_code(output) == "y = 2"

    def test_no_code(self):
        assert _extract_code({"reasoning": "done"}) is None

    def test_non_dict(self):
        assert _extract_code("just text") is None

    def test_empty_code(self):
        assert _extract_code({"code": "  "}) is None


class TestExtractLanguage:
    def test_from_output(self):
        assert _extract_language({"language": "Python"}, "") == "python"

    def test_from_step_results(self):
        output = {"step_results": [{"output": {"language": "cpp"}}]}
        assert _extract_language(output, "") == "cpp"

    def test_fallback_detection(self):
        lang = _extract_language({}, "print('hi')")
        assert lang == "python"


class TestExtractTestHints:
    def test_from_output(self):
        hints = _extract_test_hints({"test_hints": ["edge", "overflow"]})
        assert hints == ["edge", "overflow"]

    def test_from_step_results(self):
        output = {"step_results": [{"output": {"test_hints": ["empty input"]}}]}
        assert _extract_test_hints(output) == ["empty input"]

    def test_no_hints(self):
        assert _extract_test_hints({"code": "x"}) == []

    def test_non_dict(self):
        assert _extract_test_hints("text") == []


class TestSuiteToCheckerResult:
    def test_all_passed(self):
        suite = SuiteResult(passed=5, failed=0, total=5)
        result = _suite_to_checker_result(suite, "test obj")
        assert result["verdict"] == "pass"
        assert "5 tests passed" in result["reason"]
        assert result["violations"] == []

    def test_compile_error(self):
        suite = SuiteResult(passed=0, failed=3, total=3, compile_error="syntax error")
        result = _suite_to_checker_result(suite, "test obj")
        assert result["verdict"] == "fail"
        assert "Compilation failed" in result["reason"]

    def test_failures(self):
        results = [
            TestResult(name="t1", passed=True, actual="10", expected="10",
                       exec_result=ExecResult(stdout="10", stderr="", exit_code=0)),
            TestResult(name="t2", passed=False, actual="6", expected="10",
                       exec_result=ExecResult(stdout="6", stderr="", exit_code=0),
                       error="wrong answer"),
        ]
        suite = SuiteResult(passed=1, failed=1, total=2, results=results)
        result = _suite_to_checker_result(suite, "test obj")
        assert result["verdict"] == "fail"
        assert "1/2 tests failed" in result["reason"]
        assert len(result["violations"]) == 1
        assert "t2" in result["violations"][0]

    def test_timeout_failure(self):
        results = [
            TestResult(name="slow", passed=False, actual="", expected="done",
                       exec_result=ExecResult(stdout="", stderr="TIMEOUT", exit_code=-1, timed_out=True),
                       error="timeout (10.0s)"),
        ]
        suite = SuiteResult(passed=0, failed=1, total=1, results=results)
        result = _suite_to_checker_result(suite, "test obj")
        assert result["verdict"] == "fail"
        assert "Optimize" in result["suggested_fix"] or "timed out" in result["suggested_fix"]


class TestTestGeneratorService:
    def test_parse_valid_response(self):
        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "test_cases": [
                {"name": "t1", "input": "5", "expected": "10", "category": "basic"},
                {"name": "t2", "input": "0", "expected": "0", "category": "edge"},
            ],
            "coverage_notes": "basic + edge"
        }
        gen = TestGeneratorService(mock_llm)
        tests = gen.generate("double the input")
        assert len(tests) == 2
        assert tests[0].name == "t1"
        assert tests[0].input == "5"
        assert tests[0].expected == "10"
        assert tests[1].name == "t2"

    def test_parse_list_response(self):
        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = [
            {"name": "t1", "input": "1", "expected": "2"},
        ]
        gen = TestGeneratorService(mock_llm)
        tests = gen.generate("increment")
        assert len(tests) == 1

    def test_parse_empty(self):
        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {"test_cases": []}
        gen = TestGeneratorService(mock_llm)
        assert gen.generate("nothing") == []

    def test_parse_garbage(self):
        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = "not json"
        gen = TestGeneratorService(mock_llm)
        assert gen.generate("nothing") == []

    def test_parse_script_response(self):
        mock_llm = MagicMock()
        mock_llm.generate_json.return_value = {
            "test_script": {
                "language": "python",
                "code": "assert foo(1) == 2\nprint('PASS')",
                "expected_stdout": "PASS",
            },
            "coverage_notes": "unit test"
        }
        gen = TestGeneratorService(mock_llm)
        result = gen.generate_full("test foo")
        assert result.script is not None
        assert result.script.language == "python"
        assert "assert foo(1)" in result.script.code
        assert len(result.cases) == 1
        assert result.cases[0].name == "script_test"


class TestExecutionCheckerClient:
    def test_no_code_falls_back(self):
        fallback = MagicMock()
        fallback.evaluate.return_value = {"verdict": "pass", "reason": "ok",
                                           "suggested_fix": "", "confidence": 0.8, "violations": []}
        checker = ExecutionCheckerClient(
            sandbox=MagicMock(), test_gen=MagicMock(), llm_fallback=fallback
        )
        req = CheckerRequest(scope=CheckerScope.NODE, objective="test",
                             output={"reasoning": "no code here"})
        result = checker.evaluate(req)
        assert result["verdict"] == "pass"
        fallback.evaluate.assert_called_once()

    def test_no_tests_falls_back(self):
        mock_test_gen = MagicMock()
        mock_test_gen.generate_full.return_value = GeneratedTests(cases=[])
        fallback = MagicMock()
        fallback.evaluate.return_value = {"verdict": "pass", "reason": "ok",
                                           "suggested_fix": "", "confidence": 0.8, "violations": []}
        checker = ExecutionCheckerClient(
            sandbox=MagicMock(), test_gen=mock_test_gen, llm_fallback=fallback
        )
        req = CheckerRequest(scope=CheckerScope.NODE, objective="test",
                             output={"code": "print('hi')"})
        result = checker.evaluate(req)
        assert result["verdict"] == "pass"
        fallback.evaluate.assert_called_once()

    def test_execution_pass(self):
        mock_sandbox = MagicMock()
        mock_sandbox.run_tests.return_value = SuiteResult(passed=2, failed=0, total=2)
        mock_test_gen = MagicMock()
        mock_test_gen.generate_full.return_value = GeneratedTests(cases=[
            TestCase(name="t1", input="5", expected="10"),
            TestCase(name="t2", input="0", expected="0"),
        ])
        checker = ExecutionCheckerClient(
            sandbox=mock_sandbox, test_gen=mock_test_gen, llm_fallback=MagicMock()
        )
        req = CheckerRequest(scope=CheckerScope.NODE, objective="double",
                             output={"code": "n=int(input());print(n*2)", "language": "python"})
        result = checker.evaluate(req)
        assert result["verdict"] == "pass"

    def test_execution_fail(self):
        mock_sandbox = MagicMock()
        mock_sandbox.run_tests.return_value = SuiteResult(
            passed=1, failed=1, total=2,
            results=[
                TestResult(name="t1", passed=True, actual="10", expected="10",
                           exec_result=ExecResult(stdout="10", stderr="", exit_code=0)),
                TestResult(name="t2", passed=False, actual="1", expected="0",
                           exec_result=ExecResult(stdout="1", stderr="", exit_code=0),
                           error="wrong answer"),
            ]
        )
        mock_test_gen = MagicMock()
        mock_test_gen.generate_full.return_value = GeneratedTests(cases=[
            TestCase(name="t1", input="5", expected="10"),
            TestCase(name="t2", input="0", expected="0"),
        ])
        checker = ExecutionCheckerClient(
            sandbox=mock_sandbox, test_gen=mock_test_gen, llm_fallback=MagicMock()
        )
        req = CheckerRequest(scope=CheckerScope.NODE, objective="double",
                             output={"code": "n=int(input());print(n+1)", "language": "python"})
        result = checker.evaluate(req)
        assert result["verdict"] == "fail"
        assert "1/2 tests failed" in result["reason"]

    def test_script_based_pass(self):
        mock_sandbox = MagicMock()
        mock_sandbox.run_code.return_value = ExecResult(
            stdout="PASS", stderr="", exit_code=0)
        mock_test_gen = MagicMock()
        mock_test_gen.generate_full.return_value = GeneratedTests(
            cases=[TestCase(name="script_test", input="", expected="PASS")],
            script=ScriptTest(language="python", code="assert True\nprint('PASS')"),
        )
        checker = ExecutionCheckerClient(
            sandbox=mock_sandbox, test_gen=mock_test_gen, llm_fallback=MagicMock()
        )
        req = CheckerRequest(scope=CheckerScope.NODE, objective="validate module",
                             output={"code": "def foo(): return 42"})
        result = checker.evaluate(req)
        assert result["verdict"] == "pass"
        assert "Script-based" in result["reason"]

    def test_script_based_fail(self):
        mock_sandbox = MagicMock()
        mock_sandbox.run_code.return_value = ExecResult(
            stdout="", stderr="AssertionError", exit_code=1)
        mock_test_gen = MagicMock()
        mock_test_gen.generate_full.return_value = GeneratedTests(
            cases=[TestCase(name="script_test", input="", expected="PASS")],
            script=ScriptTest(language="python", code="assert False"),
        )
        checker = ExecutionCheckerClient(
            sandbox=mock_sandbox, test_gen=mock_test_gen, llm_fallback=MagicMock()
        )
        req = CheckerRequest(scope=CheckerScope.NODE, objective="validate module",
                             output={"code": "def foo(): return 0"})
        result = checker.evaluate(req)
        assert result["verdict"] == "fail"
        assert "Script-based" in result["reason"]
