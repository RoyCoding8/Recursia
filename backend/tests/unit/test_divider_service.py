from __future__ import annotations

from app.adapters.llm_client import LLMGenerateRequest, LLMResult, LLMUsage
from app.schemas.contracts import DividerDecision
from app.services.complexity import ComplexityEstimate
from app.services.divider import DividerSchemaError, DividerService, score_decomposition

_ZERO_USAGE = LLMUsage(0, 0, 0)


class StubLLMClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[LLMGenerateRequest] = []

    def generate_json(self, request: LLMGenerateRequest) -> LLMResult:
        self.calls.append(request)
        if not self._responses:
            raise AssertionError("No stub responses remaining")
        return LLMResult(data=self._responses.pop(0), usage=_ZERO_USAGE)


def test_divider_extracts_work_plan_for_base_case() -> None:
    llm = StubLLMClient(
        responses=[
            {
                "decision": "BASE_CASE",
                "rationale": "Single persona can execute this linearly.",
                "work_plan": [
                    {"step": 1, "description": "Inspect requirements"},
                    {"step": 2, "description": "Implement service"},
                    {"step": 3, "description": "Run tests"},
                ],
                "suggested_persona": "python_developer",
            }
        ]
    )
    service = DividerService(llm_client=llm, max_schema_retries=2)

    result = service.divide("Implement typed divider service", depth=0)

    assert result.decision == DividerDecision.BASE_CASE
    assert result.base_case is not None
    assert result.recursive_case is None
    assert [step["step"] for step in result.base_case.work_plan] == [1, 2, 3]
    assert result.base_case.work_plan[1]["description"] == "Implement service"
    assert result.base_case.suggested_persona == "python_developer"
    assert result.attempts_used == 1
    assert len(result.events) == 1
    assert result.events[0].event_type == "node.decomposed"
    assert result.events[0].payload["decision"] == "BASE_CASE"


def test_divider_extracts_children_for_recursive_case() -> None:
    llm = StubLLMClient(
        responses=[
            {
                "decision": "RECURSIVE_CASE",
                "rationale": "Split backend and frontend concerns.",
                "children": [
                    {
                        "objective": "Build FastAPI endpoints",
                        "dependencies": [],
                        "suggested_persona": "python_developer",
                        "interface_contract": "REST run APIs",
                    },
                    {
                        "objective": "Build React graph UI",
                        "dependencies": ["child_backend"],
                        "suggested_persona": "frontend_developer",
                        "interface_contract": "SSE event envelope",
                    },
                ],
            }
        ]
    )
    service = DividerService(llm_client=llm, max_schema_retries=2)

    result = service.divide("Deliver mission control app", depth=1)

    assert result.decision == DividerDecision.RECURSIVE_CASE
    assert result.base_case is None
    assert result.recursive_case is not None
    assert len(result.recursive_case.children) == 2
    assert result.recursive_case.children[0].objective == "Build FastAPI endpoints"
    assert result.recursive_case.children[1].dependencies == ["child_backend"]
    assert result.recursive_case.children[1].interface_contract == "SSE event envelope"
    assert result.attempts_used == 1
    assert len(result.events) == 1
    assert result.events[0].payload["decision"] == "RECURSIVE_CASE"


def test_divider_retries_and_raises_on_malformed_outputs() -> None:
    llm = StubLLMClient(
        responses=[
            {"decision": "BASE_CASE", "rationale": "missing work_plan"},
            {
                "decision": "RECURSIVE_CASE",
                "rationale": "too few children",
                "children": [{"objective": "only one child"}],
            },
            {"unexpected": "shape"},
        ]
    )
    service = DividerService(llm_client=llm, max_schema_retries=2)

    try:
        service.divide("Malformed output handling", depth=2)
        raise AssertionError("Expected DividerSchemaError")
    except DividerSchemaError as exc:
        assert "failed schema validation" in str(exc)

    assert len(llm.calls) == 3
    assert llm.calls[0].metadata["attempt"] == "1"
    assert llm.calls[1].metadata["attempt"] == "2"
    assert llm.calls[2].metadata["attempt"] == "3"


def test_score_decomposition_prefers_well_formed_recursive() -> None:
    from app.schemas.contracts import DividerRecursiveCase

    good = DividerRecursiveCase(
        decision="RECURSIVE_CASE",
        rationale="Split into backend and frontend with clear contracts.",
        children=[
            {"objective": "Build API", "dependencies": [],
             "suggested_persona": "py_dev", "interface_contract": "REST"},
            {"objective": "Build UI", "dependencies": ["Build API"],
             "suggested_persona": "fe_dev", "interface_contract": "SSE"},
        ],
    )
    bad = DividerRecursiveCase(
        decision="RECURSIVE_CASE",
        rationale="x",
        children=[
            {"objective": "thing1", "dependencies": []},
            {"objective": "thing1", "dependencies": []},  # duplicate
        ],
    )
    assert score_decomposition(good) > score_decomposition(bad)


def test_multi_candidate_picks_best() -> None:
    responses = [
        {  # candidate 1: weak (short rationale, no persona)
            "decision": "BASE_CASE",
            "rationale": "ok",
            "work_plan": [{"step": 1, "description": "do it"}],
        },
        {  # candidate 2: strong (good structure)
            "decision": "BASE_CASE",
            "rationale": "Comprehensive three-step plan with clear separation.",
            "work_plan": [
                {"step": 1, "description": "Analyze requirements"},
                {"step": 2, "description": "Implement solution"},
                {"step": 3, "description": "Verify with tests"},
            ],
            "suggested_persona": "engineer",
        },
        {  # candidate 3: medium
            "decision": "BASE_CASE",
            "rationale": "Two steps should suffice for this task.",
            "work_plan": [
                {"step": 1, "description": "Plan"},
                {"step": 2, "description": "Execute"},
            ],
        },
    ]
    llm = StubLLMClient(responses=responses)
    service = DividerService(llm_client=llm, max_schema_retries=0)

    result = service.divide("Build feature", depth=0, num_candidates=3)

    assert result.decision == DividerDecision.BASE_CASE
    assert result.candidates_generated == 3
    assert result.base_case is not None
    # Best candidate should have 3 work_plan steps and persona
    assert len(result.base_case.work_plan) == 3
    assert result.base_case.suggested_persona == "engineer"


def test_complexity_hint_injected_into_prompt() -> None:
    llm = StubLLMClient(responses=[{
        "decision": "BASE_CASE",
        "rationale": "Simple task, linear plan.",
        "work_plan": [{"step": 1, "description": "Do it"}],
    }])
    service = DividerService(llm_client=llm)
    cx = ComplexityEstimate(score=0.75, suggested_depth=5,
                            model_tier="strong", reasoning="high complexity")

    service.divide("Build system", depth=0, complexity=cx, num_candidates=1)

    prompt = llm.calls[0].messages[1].content
    assert "Complexity" in prompt
    assert "high complexity" in prompt
    assert "Suggested max depth: 5" in prompt
