"""Tests for sandbox executor — subprocess code runner."""
from __future__ import annotations

from app.sandbox.executor import (
    ExecResult,
    Language,
    SandboxExecutor,
    SuiteResult,
    TestCase,
    detect_language,
)


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("def foo():\n    print('hi')") == Language.PYTHON

    def test_javascript(self):
        assert detect_language("const x = 1;\nconsole.log(x);") == Language.JAVASCRIPT

    def test_cpp(self):
        assert detect_language('#include <iostream>\nint main() { std::cout << "hi"; }') == Language.CPP

    def test_java(self):
        assert detect_language("public class Main { public static void main(String[] args) {} }") == Language.JAVA

    def test_fallback_python(self):
        assert detect_language("x = 1") == Language.PYTHON


class TestSandboxExecutorRunCode:
    def test_python_hello(self):
        sb = SandboxExecutor(timeout_s=10.0)
        r = sb.run_code("print('hello')", language=Language.PYTHON)
        assert r.ok
        assert r.stdout.strip() == "hello"
        assert r.exit_code == 0
        assert not r.timed_out

    def test_python_stdin(self):
        sb = SandboxExecutor(timeout_s=10.0)
        r = sb.run_code("x = input()\nprint(f'got {x}')", language=Language.PYTHON, stdin="test_input")
        assert r.ok
        assert r.stdout.strip() == "got test_input"

    def test_python_error(self):
        sb = SandboxExecutor(timeout_s=10.0)
        r = sb.run_code("raise ValueError('boom')", language=Language.PYTHON)
        assert not r.ok
        assert r.exit_code != 0
        assert "ValueError" in r.stderr

    def test_python_timeout(self):
        sb = SandboxExecutor(timeout_s=1.0)
        r = sb.run_code("import time; time.sleep(10)", language=Language.PYTHON, timeout_s=1.0)
        assert r.timed_out
        assert not r.ok

    def test_auto_detect_language(self):
        sb = SandboxExecutor(timeout_s=10.0)
        r = sb.run_code("print(42)")
        assert r.ok
        assert r.stdout.strip() == "42"

    def test_string_language_param(self):
        sb = SandboxExecutor(timeout_s=10.0)
        r = sb.run_code("print('hi')", language="python")
        assert r.ok

    def test_max_output_truncation(self):
        sb = SandboxExecutor(timeout_s=10.0, max_output_bytes=20)
        r = sb.run_code("print('a' * 100)", language=Language.PYTHON)
        assert len(r.stdout) <= 20


class TestSandboxExecutorRunTests:
    def test_all_pass(self):
        code = "n = int(input())\nprint(n * 2)"
        tests = [
            TestCase(name="basic", input="5", expected="10"),
            TestCase(name="zero", input="0", expected="0"),
            TestCase(name="negative", input="-3", expected="-6"),
        ]
        sb = SandboxExecutor(timeout_s=10.0)
        suite = sb.run_tests(code, tests, language=Language.PYTHON)
        assert suite.all_passed
        assert suite.passed == 3
        assert suite.failed == 0
        assert suite.total == 3

    def test_wrong_answer(self):
        code = "n = int(input())\nprint(n + 1)"  # deliberately wrong
        tests = [
            TestCase(name="basic", input="5", expected="10"),
        ]
        sb = SandboxExecutor(timeout_s=10.0)
        suite = sb.run_tests(code, tests, language=Language.PYTHON)
        assert not suite.all_passed
        assert suite.failed == 1
        assert suite.results[0].error == "wrong answer"
        assert suite.results[0].actual == "6"

    def test_runtime_error(self):
        code = "n = int(input())\nprint(1 // n)"  # div by zero on input 0
        tests = [
            TestCase(name="basic", input="5", expected="0"),
            TestCase(name="zero_div", input="0", expected="error"),
        ]
        sb = SandboxExecutor(timeout_s=10.0)
        suite = sb.run_tests(code, tests, language=Language.PYTHON)
        assert not suite.all_passed
        zero_result = suite.results[1]
        assert not zero_result.passed
        assert zero_result.error is not None

    def test_timeout_in_test(self):
        code = "import time; time.sleep(10); print('done')"
        tests = [TestCase(name="slow", input="", expected="done", timeout_s=1.0)]
        sb = SandboxExecutor(timeout_s=10.0)
        suite = sb.run_tests(code, tests, language=Language.PYTHON)
        assert not suite.all_passed
        assert "timeout" in (suite.results[0].error or "")


class TestExecResult:
    def test_ok_true(self):
        r = ExecResult(stdout="hi", stderr="", exit_code=0)
        assert r.ok

    def test_ok_false_exit(self):
        r = ExecResult(stdout="", stderr="err", exit_code=1)
        assert not r.ok

    def test_ok_false_timeout(self):
        r = ExecResult(stdout="", stderr="", exit_code=0, timed_out=True)
        assert not r.ok


class TestSuiteResult:
    def test_all_passed(self):
        s = SuiteResult(passed=3, failed=0, total=3)
        assert s.all_passed

    def test_not_all_passed(self):
        s = SuiteResult(passed=2, failed=1, total=3)
        assert not s.all_passed

    def test_compile_error(self):
        s = SuiteResult(passed=0, failed=0, total=0, compile_error="syntax error")
        assert not s.all_passed
