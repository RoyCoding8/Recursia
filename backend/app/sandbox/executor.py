"""Sandbox executor — runs untrusted code with pluggable isolation backends.

Backends:
- SubprocessBackend: local subprocess, dev/testing only (no container isolation)
- EpicboxBackend: Docker containers via epicbox (production, resource limits, isolation)

Select backend via SANDBOX_BACKEND env var: "local" (default) or "docker".
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, Sequence


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    CPP = "cpp"
    JAVA = "java"


@dataclass(slots=True, frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass(slots=True, frozen=True)
class TestCase:
    name: str
    input: str
    expected: str
    timeout_s: float = 10.0


@dataclass(slots=True, frozen=True)
class TestResult:
    name: str
    passed: bool
    actual: str
    expected: str
    exec_result: ExecResult
    error: str | None = None


@dataclass(slots=True, frozen=True)
class SuiteResult:
    passed: int
    failed: int
    total: int
    results: list[TestResult] = field(default_factory=list)
    compile_error: str | None = None

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.compile_error is None


# (file_ext, compile_cmd_template | None, run_cmd_template)
_LANG_CONFIG: dict[Language, tuple[str, str | None, str]] = {
    Language.PYTHON: (".py", None, "python {file}"),
    Language.JAVASCRIPT: (".js", None, "node {file}"),
    Language.CPP: (".cpp", "g++ -O2 -std=c++17 -o {binary} {file}", "{binary}"),
    Language.JAVA: (".java", "javac {file}", "java -cp {dir} Main"),
}


def detect_language(code: str) -> Language:
    markers = {
        Language.PYTHON: ("def ", "import ", "print(", "if __name__"),
        Language.JAVASCRIPT: ("const ", "let ", "function ", "console.log", "require("),
        Language.CPP: ("#include", "int main", "std::", "cout", "cin"),
        Language.JAVA: ("public class", "public static void main", "System.out"),
    }
    scores = {lang: sum(1 for m in ms if m in code) for lang, ms in markers.items()}
    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    return best if scores[best] > 0 else Language.PYTHON


def _resolve_lang(language: Language | str | None, code: str) -> Language:
    if language is None:
        return detect_language(code)
    if isinstance(language, Language):
        return language
    try:
        return Language(language.lower())
    except ValueError:
        return detect_language(code)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class ExecutionBackend(Protocol):
    """Interface for code execution backends."""
    def run_code(self, code: str, lang: Language, *,
                 stdin: str, timeout_s: float) -> ExecResult: ...

    def run_batch(self, code: str, lang: Language,
                  cases: Sequence[tuple[str, float]]) -> list[ExecResult]: ...


class SubprocessBackend:
    """Local subprocess — dev/testing only. No container isolation."""

    def __init__(self, *, max_output_bytes: int = 1_000_000):
        self._max_out = max_output_bytes

    def run_code(self, code: str, lang: Language, *,
                 stdin: str = "", timeout_s: float = 30.0) -> ExecResult:
        workdir = tempfile.mkdtemp(prefix="recursia_sandbox_")
        try:
            src = self._write_source(workdir, code, lang)
            comp = self._compile(workdir, src, lang, timeout_s)
            if comp and not comp.ok:
                return ExecResult(stdout="", stderr=f"COMPILE ERROR:\n{comp.stderr}",
                                  exit_code=comp.exit_code, timed_out=comp.timed_out,
                                  duration_ms=comp.duration_ms)
            return self._run(workdir, src, lang, stdin, timeout_s)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def run_batch(self, code: str, lang: Language,
                  cases: Sequence[tuple[str, float]]) -> list[ExecResult]:
        workdir = tempfile.mkdtemp(prefix="recursia_sandbox_")
        try:
            src = self._write_source(workdir, code, lang)
            max_t = max((t for _, t in cases), default=30.0)
            comp = self._compile(workdir, src, lang, max_t)
            if comp and not comp.ok:
                err = ExecResult(stdout="", stderr=f"COMPILE ERROR:\n{comp.stderr}",
                                 exit_code=comp.exit_code, timed_out=comp.timed_out,
                                 duration_ms=comp.duration_ms)
                return [err] * len(cases)
            return [self._run(workdir, src, lang, sin, t) for sin, t in cases]
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _write_source(workdir: str, code: str, lang: Language) -> Path:
        ext = _LANG_CONFIG[lang][0]
        src = Path(workdir) / f"solution{ext}"
        src.write_text(code, encoding="utf-8")
        return src

    def _compile(self, workdir: str, src: Path, lang: Language,
                 timeout_s: float) -> ExecResult | None:
        compile_tpl = _LANG_CONFIG[lang][1]
        if not compile_tpl:
            return None
        binary = Path(workdir) / "solution"
        cmd = compile_tpl.format(file=str(src), binary=str(binary), dir=workdir)
        return self._exec(cmd, workdir, "", min(timeout_s, 30.0))

    def _run(self, workdir: str, src: Path, lang: Language,
             stdin: str, timeout_s: float) -> ExecResult:
        _, _, run_tpl = _LANG_CONFIG[lang]
        binary = Path(workdir) / "solution"
        cmd = run_tpl.format(file=str(src), binary=str(binary), dir=workdir)
        return self._exec(cmd, workdir, stdin, timeout_s)

    def _exec(self, cmd: str, cwd: str, stdin: str, timeout: float) -> ExecResult:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=cwd, input=stdin, capture_output=True,
                text=True, timeout=timeout, env=env,
            )
            elapsed = int((time.monotonic() - start) * 1000)
            return ExecResult(
                stdout=proc.stdout[:self._max_out], stderr=proc.stderr[:self._max_out],
                exit_code=proc.returncode, duration_ms=elapsed,
            )
        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - start) * 1000)
            return ExecResult(stdout="", stderr="TIMEOUT", exit_code=-1,
                              timed_out=True, duration_ms=elapsed)


class EpicboxBackend:
    """Docker-based isolated execution via epicbox. Requires: pip install epicbox"""

    _PROFILES: dict[Language, tuple[str, str, str]] = {
        Language.PYTHON: ("python", "python:3.11-alpine", "python3 solution.py"),
        Language.JAVASCRIPT: ("node", "node:20-alpine", "node solution.js"),
        Language.CPP: ("gcc", "gcc:13",
                       "sh -c 'g++ -O2 -std=c++17 -o solution solution.cpp && ./solution'"),
        Language.JAVA: ("java", "eclipse-temurin:21-alpine",
                        "sh -c 'javac Main.java && java -cp . Main'"),
    }

    def __init__(self, *, memory_mb: int = 256, max_output_bytes: int = 1_000_000):
        self._mem = memory_mb
        self._max_out = max_output_bytes
        self._configured = False

    def _ensure_configured(self) -> None:
        if self._configured:
            return
        import epicbox  # type: ignore[import-untyped]
        profiles = [epicbox.Profile(name, image)
                    for name, image, _ in self._PROFILES.values()]
        epicbox.configure(profiles=profiles)
        self._configured = True

    def run_code(self, code: str, lang: Language, *,
                 stdin: str = "", timeout_s: float = 30.0) -> ExecResult:
        import epicbox  # type: ignore[import-untyped]
        self._ensure_configured()
        profile, _, cmd = self._PROFILES[lang]
        ext = _LANG_CONFIG[lang][0]
        fname = "Main.java" if lang == Language.JAVA else f"solution{ext}"
        files = [{"name": fname, "content": code.encode("utf-8")}]
        limits = {"cputime": max(int(timeout_s), 1), "memory": self._mem,
                  "realtime": max(int(timeout_s * 2), 2)}
        start = time.monotonic()
        result = epicbox.run(profile, cmd, files=files,
                             stdin=stdin.encode("utf-8") if stdin else None,
                             limits=limits)
        elapsed = int((time.monotonic() - start) * 1000)
        return ExecResult(
            stdout=result["stdout"].decode("utf-8", errors="replace")[:self._max_out],
            stderr=result["stderr"].decode("utf-8", errors="replace")[:self._max_out],
            exit_code=result["exit_code"],
            timed_out=result.get("timeout", False),
            duration_ms=elapsed,
        )

    def run_batch(self, code: str, lang: Language,
                  cases: Sequence[tuple[str, float]]) -> list[ExecResult]:
        return [self.run_code(code, lang, stdin=s, timeout_s=t) for s, t in cases]


# ---------------------------------------------------------------------------
# Executor (public API, delegates to backend)
# ---------------------------------------------------------------------------

class SandboxExecutor:
    """Runs code against a pluggable backend. Backward-compatible constructor."""

    def __init__(self, *, backend: ExecutionBackend | None = None,
                 timeout_s: float = 30.0, max_output_bytes: int = 1_000_000):
        self._backend = backend or SubprocessBackend(max_output_bytes=max_output_bytes)
        self._timeout = timeout_s

    def run_code(self, code: str, *, language: Language | str | None = None,
                 stdin: str = "", timeout_s: float | None = None) -> ExecResult:
        lang = _resolve_lang(language, code)
        return self._backend.run_code(code, lang, stdin=stdin,
                                      timeout_s=timeout_s or self._timeout)

    def run_tests(self, code: str, tests: list[TestCase], *,
                  language: Language | str | None = None) -> SuiteResult:
        lang = _resolve_lang(language, code)
        cases = [(tc.input, tc.timeout_s) for tc in tests]
        exec_results = self._backend.run_batch(code, lang, cases)

        # Detect compile error (all results share same failure)
        if exec_results and "COMPILE ERROR" in exec_results[0].stderr:
            return SuiteResult(passed=0, failed=len(tests), total=len(tests),
                               compile_error=exec_results[0].stderr.strip())

        results: list[TestResult] = []
        passed = 0
        for tc, er in zip(tests, exec_results):
            actual, expected = er.stdout.rstrip(), tc.expected.rstrip()
            ok = actual == expected and er.ok
            error = None
            if er.timed_out:
                error = f"timeout ({tc.timeout_s}s)"
            elif not er.ok:
                error = er.stderr.strip()[:500] if er.stderr else f"exit {er.exit_code}"
            elif not ok:
                error = "wrong answer"
            if ok:
                passed += 1
            results.append(TestResult(name=tc.name, passed=ok, actual=actual,
                                      expected=expected, exec_result=er, error=error))
        return SuiteResult(passed=passed, failed=len(tests) - passed,
                           total=len(tests), results=results)


def create_sandbox(*, backend: str | None = None, timeout_s: float = 30.0,
                   **kwargs) -> SandboxExecutor:
    """Factory: create SandboxExecutor with appropriate backend.

    backend="local" (default): SubprocessBackend — dev/testing
    backend="docker": EpicboxBackend — production (requires epicbox + Docker)
    """
    name = backend or os.getenv("SANDBOX_BACKEND", "local")
    if name == "docker":
        return SandboxExecutor(backend=EpicboxBackend(**kwargs), timeout_s=timeout_s)
    return SandboxExecutor(backend=SubprocessBackend(**kwargs), timeout_s=timeout_s)


__all__ = [
    "EpicboxBackend", "ExecResult", "ExecutionBackend", "Language",
    "SandboxExecutor", "SubprocessBackend", "SuiteResult",
    "TestCase", "TestResult", "create_sandbox", "detect_language",
]
