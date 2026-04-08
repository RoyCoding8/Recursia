from __future__ import annotations

from app.domain.models import NodeStatus
from app.schemas.api import CheckerConfig
from app.services.checker import (
    CheckerRequest,
    CheckerScope,
    CheckerService,
)


class FakeCheckerClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[CheckerRequest] = []

    def evaluate(self, request: CheckerRequest) -> object:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("No fake checker responses remaining")
        return self._responses.pop(0)


def test_checker_globally_off_skips_invocation() -> None:
    client = FakeCheckerClient(
        responses=[
            {
                "verdict": "pass",
                "reason": "would pass",
                "suggested_fix": "none",
                "confidence": 0.95,
                "violations": [],
            }
        ]
    )
    service = CheckerService(checker_client=client)
    config = CheckerConfig(enabled=False, node_level=True, merge_level=True)

    outcome = service.evaluate_node(
        checker_config=config,
        objective="Validate node output",
        output={"value": 1},
    )

    assert outcome.invoked is False
    assert outcome.result is None
    assert outcome.next_node_status is None
    assert outcome.consecutive_failures == 0
    assert outcome.should_block_human is False
    assert outcome.skipped_reason == "checker disabled for selected scope"
    assert client.calls == []


def test_node_level_only_skips_merge_checker() -> None:
    client = FakeCheckerClient(
        responses=[
            {
                "verdict": "pass",
                "reason": "node output satisfies constraints",
                "suggested_fix": "none",
                "confidence": 0.9,
                "violations": [],
            },
            {
                "verdict": "pass",
                "reason": "merge would also pass",
                "suggested_fix": "none",
                "confidence": 0.91,
                "violations": [],
            },
        ]
    )
    service = CheckerService(checker_client=client)
    config = CheckerConfig(enabled=True, node_level=True, merge_level=False)

    node_outcome = service.evaluate_node(
        checker_config=config,
        objective="Node output objective",
        output={"artifact": "node-result"},
    )
    merge_outcome = service.evaluate_merge(
        checker_config=config,
        objective="Merge output objective",
        output={"artifact": "merged-result"},
    )

    assert node_outcome.invoked is True
    assert node_outcome.result is not None
    assert node_outcome.result.verdict.value == "pass"
    assert node_outcome.next_node_status == NodeStatus.COMPLETED

    assert merge_outcome.invoked is False
    assert merge_outcome.result is None
    assert merge_outcome.skipped_reason == "checker disabled for selected scope"

    assert len(client.calls) == 1
    assert client.calls[0].scope == CheckerScope.NODE


def test_three_consecutive_fails_emit_blocked_human_outcome() -> None:
    client = FakeCheckerClient(
        responses=[
            {
                "verdict": "fail",
                "reason": "missing required constraints",
                "suggested_fix": "add constraints section",
                "confidence": 0.62,
                "violations": ["constraints_missing"],
            },
            {
                "verdict": "fail",
                "reason": "still missing constraints",
                "suggested_fix": "include constraints and assumptions",
                "confidence": 0.65,
                "violations": ["constraints_missing"],
            },
            {
                "verdict": "fail",
                "reason": "third failure",
                "suggested_fix": "human review required",
                "confidence": 0.7,
                "violations": ["quality_gate_failed"],
            },
        ]
    )
    service = CheckerService(checker_client=client)
    config = CheckerConfig(enabled=True, node_level=True, merge_level=True)

    outcome_1 = service.evaluate_node(
        checker_config=config,
        objective="Objective",
        output={"attempt": 1},
        consecutive_failures=0,
    )
    outcome_2 = service.evaluate_node(
        checker_config=config,
        objective="Objective",
        output={"attempt": 2},
        consecutive_failures=outcome_1.consecutive_failures,
    )
    outcome_3 = service.evaluate_node(
        checker_config=config,
        objective="Objective",
        output={"attempt": 3},
        consecutive_failures=outcome_2.consecutive_failures,
    )

    assert outcome_1.consecutive_failures == 1
    assert outcome_1.next_node_status == NodeStatus.FAILED_CHECK
    assert outcome_1.should_block_human is False

    assert outcome_2.consecutive_failures == 2
    assert outcome_2.next_node_status == NodeStatus.FAILED_CHECK
    assert outcome_2.should_block_human is False

    assert outcome_3.consecutive_failures == 3
    assert outcome_3.next_node_status == NodeStatus.BLOCKED_HUMAN
    assert outcome_3.should_block_human is True
    assert any(event.event_type == "node.blocked_human" for event in outcome_3.events)

    blocked_event = [
        e for e in outcome_3.events if e.event_type == "node.blocked_human"
    ][0]
    assert blocked_event.payload["reason"] == "checker_failed_consecutive_threshold"
    assert blocked_event.payload["consecutive_failures"] == 3
    assert blocked_event.payload["threshold"] == 3
    assert len(client.calls) == 3
