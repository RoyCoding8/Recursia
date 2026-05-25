"""Tests for checker_handler, UsageTrackingClient, NodeContext, policies."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.adapters.llm_client import (
    LLMGenerateRequest,
    LLMMessage,
    LLMResult,
    LLMUsage,
    UsageTrackingClient,
)
from app.domain.enums import NodeStatus, RunStatus
from app.domain.models import NodeContext, NodeState
from app.domain.policies import (
    CheckerFailurePolicy,
    InvalidTransitionError,
    ensure_node_transition,
    ensure_run_transition,
)
from app.schemas.contracts import CheckerResult
from app.services.checker import CheckerOutcome, CheckerScope
from app.services.checker_handler import build_checker_feedback, retry_checker_loop

# --- build_checker_feedback ---

class TestBuildCheckerFeedback:
    def test_none_returns_empty(self):
        assert build_checker_feedback(None) == ""

    def test_fix_only_no_violations(self):
        result = CheckerResult(
            verdict="fail", reason="bad", suggested_fix="do better",
            confidence=0.5, violations=[],
        )
        assert build_checker_feedback(result) == "do better"

    def test_fix_with_violations(self):
        result = CheckerResult(
            verdict="fail", reason="bad", suggested_fix="fix it",
            confidence=0.3, violations=["missing edge case", "wrong output"],
        )
        fb = build_checker_feedback(result)
        assert "fix it" in fb
        assert "missing edge case" in fb
        assert "wrong output" in fb

    def test_empty_violations_list(self):
        result = CheckerResult(
            verdict="fail", reason="bad", suggested_fix="fix", confidence=0.5, violations=[],
        )
        assert build_checker_feedback(result) == "fix"


# --- retry_checker_loop ---

def _make_outcome(status: NodeStatus, result: CheckerResult | None = None,
                  invoked: bool = True, failures: int = 0, should_block: bool = False) -> CheckerOutcome:
    return CheckerOutcome(
        invoked=invoked, scope=CheckerScope.NODE, result=result,
        consecutive_failures=failures, should_block_human=should_block,
        next_node_status=status, attempts_used=1,
    )


class TestRetryCheckerLoop:
    def _make_node(self):
        return NodeState(node_id="n1", run_id="r1", objective="test")

    def test_pass_on_first_try(self):
        node = self._make_node()
        pass_result = CheckerResult(verdict="pass", reason="ok", suggested_fix="n/a",
                                    confidence=1.0, violations=[])
        outcome_pass = _make_outcome(NodeStatus.COMPLETED, pass_result)
        call_count = 0

        def action():
            nonlocal call_count
            call_count += 1
            return {"result": "ok"}

        def evaluate(**_kwargs):
            return outcome_pass

        output, final_outcome, final_result = retry_checker_loop(
            evaluate_checker=evaluate, node=node,
            checker_outcome=outcome_pass, checker_result=pass_result,
            max_retries=3, action=action, scope=CheckerScope.NODE,
        )
        assert call_count == 1
        assert output == {"result": "ok"}
        assert final_outcome.next_node_status == NodeStatus.COMPLETED

    def test_retries_until_pass(self):
        node = self._make_node()
        fail_result = CheckerResult(verdict="fail", reason="bad", suggested_fix="fix",
                                    confidence=0.5, violations=[])
        pass_result = CheckerResult(verdict="pass", reason="ok", suggested_fix="n/a",
                                    confidence=1.0, violations=[])
        fail_outcome = _make_outcome(NodeStatus.FAILED_CHECK, fail_result)
        pass_outcome = _make_outcome(NodeStatus.COMPLETED, pass_result)
        call_count = 0

        def action():
            nonlocal call_count
            call_count += 1
            return call_count

        def evaluate(**_kwargs):
            return pass_outcome if call_count >= 2 else fail_outcome

        output, final_outcome, _ = retry_checker_loop(
            evaluate_checker=evaluate, node=node,
            checker_outcome=fail_outcome, checker_result=fail_result,
            max_retries=3, action=action, scope=CheckerScope.NODE,
        )
        assert call_count == 2
        assert output == 2
        assert final_outcome.next_node_status == NodeStatus.COMPLETED

    def test_on_retry_callback_receives_output(self):
        node = self._make_node()
        pass_result = CheckerResult(verdict="pass", reason="ok", suggested_fix="n/a",
                                    confidence=1.0, violations=[])
        pass_outcome = _make_outcome(NodeStatus.COMPLETED, pass_result)
        callback_outputs = []

        def on_retry(output, checker_result):
            callback_outputs.append(output)

        def action():
            return "step_output"

        def evaluate(**_kwargs):
            return pass_outcome

        retry_checker_loop(
            evaluate_checker=evaluate, node=node,
            checker_outcome=pass_outcome, checker_result=pass_result,
            max_retries=2, action=action, scope=CheckerScope.NODE,
            on_retry=on_retry,
        )
        assert callback_outputs == ["step_output"]

    def test_none_outcome_breaks_loop(self):
        node = self._make_node()
        call_count = 0

        def action():
            nonlocal call_count
            call_count += 1
            return call_count

        def evaluate(**_kwargs):
            return None

        output, final_outcome, _ = retry_checker_loop(
            evaluate_checker=evaluate, node=node,
            checker_outcome=None, checker_result=None,
            max_retries=5, action=action, scope=CheckerScope.NODE,
        )
        assert call_count == 1
        assert final_outcome is None

    def test_blocked_human_breaks_loop(self):
        node = self._make_node()
        blocked_result = CheckerResult(verdict="fail", reason="blocked", suggested_fix="n/a",
                                       confidence=0.0, violations=[])
        blocked_outcome = _make_outcome(NodeStatus.BLOCKED_HUMAN, blocked_result)

        def action():
            return "x"

        def evaluate(**_kwargs):
            return blocked_outcome

        _, final_outcome, _ = retry_checker_loop(
            evaluate_checker=evaluate, node=node,
            checker_outcome=blocked_outcome, checker_result=blocked_result,
            max_retries=3, action=action, scope=CheckerScope.NODE,
        )
        assert final_outcome.next_node_status == NodeStatus.BLOCKED_HUMAN


# --- UsageTrackingClient ---

class TestUsageTrackingClient:
    def _make_inner(self, usage: LLMUsage):
        inner = MagicMock()
        inner.generate_json.return_value = LLMResult(data={"ok": True}, usage=usage)
        return inner

    def test_accumulates_tokens(self):
        inner = self._make_inner(LLMUsage(100, 50, 150))
        tracker = UsageTrackingClient(inner)
        req = LLMGenerateRequest(messages=[LLMMessage("user", "hi")])

        tracker.generate_json(req)
        tracker.generate_json(req)

        assert tracker.total_prompt_tokens == 200
        assert tracker.total_completion_tokens == 100
        assert tracker.total_tokens == 300

    def test_accumulates_cost(self):
        inner = self._make_inner(LLMUsage(10, 5, 15, cost_usd=0.01))
        tracker = UsageTrackingClient(inner)
        req = LLMGenerateRequest(messages=[LLMMessage("user", "hi")])

        tracker.generate_json(req)
        tracker.generate_json(req)

        assert tracker.total_cost_usd == pytest.approx(0.02)

    def test_none_cost_not_accumulated(self):
        inner = self._make_inner(LLMUsage(10, 5, 15, cost_usd=None))
        tracker = UsageTrackingClient(inner)
        req = LLMGenerateRequest(messages=[LLMMessage("user", "hi")])

        tracker.generate_json(req)
        assert tracker.total_cost_usd == 0.0

    def test_returns_inner_result(self):
        inner = self._make_inner(LLMUsage(10, 5, 15))
        tracker = UsageTrackingClient(inner)
        req = LLMGenerateRequest(messages=[LLMMessage("user", "hi")])

        result = tracker.generate_json(req)
        assert result.data == {"ok": True}
        assert result.usage.total_tokens == 15


# --- NodeContext ---

class TestNodeContext:
    def test_child_inherits_root(self):
        ctx = NodeContext(root_objective="build app")
        child = ctx.child("implement auth", siblings=["setup db"])
        assert child.root_objective == "build app"
        assert child.parent_chain == ("implement auth",)
        assert child.sibling_objectives == ("setup db",)
        assert child.checker_feedback is None
        assert child.prior_persona_output is None

    def test_child_inherits_feedback(self):
        ctx = NodeContext(root_objective="x", checker_feedback="fix it",
                         prior_persona_output="prior")
        child = ctx.child("y")
        assert child.checker_feedback == "fix it"
        assert child.prior_persona_output == "prior"

    def test_with_prior_output(self):
        ctx = NodeContext(root_objective="x")
        ctx2 = ctx.with_prior_output("output from planner")
        assert ctx2.prior_persona_output == "output from planner"
        assert ctx2.root_objective == "x"
        # original unchanged (frozen)
        assert ctx.prior_persona_output is None

    def test_with_sibling_output(self):
        ctx = NodeContext(root_objective="x")
        ctx2 = ctx.with_sibling_output("sibling A done")
        assert ctx2.completed_sibling_summaries == ("sibling A done",)
        ctx3 = ctx2.with_sibling_output("sibling B done")
        assert ctx3.completed_sibling_summaries == ("sibling A done", "sibling B done")

    def test_with_checker_feedback(self):
        ctx = NodeContext(root_objective="x")
        ctx2 = ctx.with_checker_feedback("add null check", ["crash on empty input"])
        fb = ctx2.checker_feedback
        assert "add null check" in fb
        assert "crash on empty input" in fb

    def test_with_checker_feedback_no_violations(self):
        ctx = NodeContext(root_objective="x")
        ctx2 = ctx.with_checker_feedback("fix it", [])
        assert "fix it" in ctx2.checker_feedback
        assert "Violations" not in ctx2.checker_feedback

    def test_to_prompt_block_minimal(self):
        ctx = NodeContext(root_objective="build API")
        block = ctx.to_prompt_block()
        assert "Root goal: build API" in block
        assert "Decomposition" not in block

    def test_to_prompt_block_full(self):
        ctx = NodeContext(
            root_objective="build app",
            parent_chain=("root", "auth"),
            sibling_objectives=("db setup",),
            completed_sibling_summaries=("db: done",),
            boundary_constraints=("no ORM",),
            checker_feedback="fix it",
            prior_persona_output="planner said X",
        )
        block = ctx.to_prompt_block()
        assert "Root goal: build app" in block
        assert "root → auth" in block
        assert "db setup" in block
        assert "db: done" in block
        assert "no ORM" in block
        assert "fix it" in block
        assert "planner said X" in block


# --- Policies ---

class TestEnsureRunTransition:
    def test_valid_transitions(self):
        ensure_run_transition(RunStatus.QUEUED, RunStatus.RUNNING)
        ensure_run_transition(RunStatus.RUNNING, RunStatus.COMPLETED)
        ensure_run_transition(RunStatus.RUNNING, RunStatus.FAILED)
        ensure_run_transition(RunStatus.RUNNING, RunStatus.BLOCKED_HUMAN)
        ensure_run_transition(RunStatus.BLOCKED_HUMAN, RunStatus.RUNNING)
        ensure_run_transition(RunStatus.QUEUED, RunStatus.CANCELED)

    def test_invalid_transition_raises(self):
        with pytest.raises(InvalidTransitionError):
            ensure_run_transition(RunStatus.QUEUED, RunStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            ensure_run_transition(RunStatus.COMPLETED, RunStatus.RUNNING)
        with pytest.raises(InvalidTransitionError):
            ensure_run_transition(RunStatus.FAILED, RunStatus.RUNNING)
        with pytest.raises(InvalidTransitionError):
            ensure_run_transition(RunStatus.CANCELED, RunStatus.QUEUED)

    def test_terminal_states_have_no_outgoing(self):
        for terminal in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELED):
            for target in RunStatus:
                if target == terminal:
                    continue
                with pytest.raises(InvalidTransitionError):
                    ensure_run_transition(terminal, target)


class TestEnsureNodeTransition:
    def test_valid_transitions(self):
        ensure_node_transition(NodeStatus.QUEUED, NodeStatus.RUNNING)
        ensure_node_transition(NodeStatus.RUNNING, NodeStatus.WAITING_CHECK)
        ensure_node_transition(NodeStatus.WAITING_CHECK, NodeStatus.COMPLETED)
        ensure_node_transition(NodeStatus.WAITING_CHECK, NodeStatus.FAILED_CHECK)
        ensure_node_transition(NodeStatus.FAILED_CHECK, NodeStatus.RUNNING)
        ensure_node_transition(NodeStatus.FAILED_CHECK, NodeStatus.BLOCKED_HUMAN)

    def test_invalid_transition_raises(self):
        with pytest.raises(InvalidTransitionError):
            ensure_node_transition(NodeStatus.QUEUED, NodeStatus.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            ensure_node_transition(NodeStatus.RUNNING, NodeStatus.BLOCKED_HUMAN)
        with pytest.raises(InvalidTransitionError):
            ensure_node_transition(NodeStatus.COMPLETED, NodeStatus.RUNNING)

    def test_terminal_states_have_no_outgoing(self):
        for terminal in (NodeStatus.COMPLETED, NodeStatus.ERROR):
            for target in NodeStatus:
                if target == terminal:
                    continue
                with pytest.raises(InvalidTransitionError):
                    ensure_node_transition(terminal, target)


class TestCheckerFailurePolicy:
    def test_default_threshold(self):
        policy = CheckerFailurePolicy()
        assert not policy.should_block(0)
        assert not policy.should_block(2)
        assert policy.should_block(3)
        assert policy.should_block(5)

    def test_custom_threshold(self):
        policy = CheckerFailurePolicy(block_after_consecutive_failures=1)
        assert not policy.should_block(0)
        assert policy.should_block(1)
        assert policy.should_block(2)


# --- NodeState timing ---

class TestNodeStateTiming:
    def test_mark_first_token_computes_ttft(self):
        from datetime import UTC, datetime, timedelta
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        token_time = start + timedelta(milliseconds=150)
        node = NodeState(node_id="n1", run_id="r1", objective="test")
        node.mark_running(at=start)
        node.mark_first_token(at=token_time)
        assert node.ttft_ms == 150

    def test_mark_first_token_idempotent(self):
        from datetime import UTC, datetime, timedelta
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        t1 = start + timedelta(milliseconds=100)
        t2 = start + timedelta(milliseconds=200)
        node = NodeState(node_id="n1", run_id="r1", objective="test")
        node.mark_running(at=start)
        node.mark_first_token(at=t1)
        node.mark_first_token(at=t2)
        assert node.ttft_ms == 100  # first wins

    def test_mark_ended_computes_duration(self):
        from datetime import UTC, datetime, timedelta
        start = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        end = start + timedelta(seconds=5)
        node = NodeState(node_id="n1", run_id="r1", objective="test")
        node.mark_running(at=start)
        node.mark_ended(NodeStatus.COMPLETED, at=end)
        assert node.duration_ms == 5000

    def test_mark_first_token_no_started_at(self):
        node = NodeState(node_id="n1", run_id="r1", objective="test")
        node.mark_first_token()
        assert node.first_token_at is not None
        assert node.ttft_ms is None  # no started_at

    def test_mark_ended_no_started_at(self):
        node = NodeState(node_id="n1", run_id="r1", objective="test")
        node.mark_ended(NodeStatus.COMPLETED)
        assert node.duration_ms is None
