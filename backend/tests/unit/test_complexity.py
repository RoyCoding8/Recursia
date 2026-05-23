"""Tests for complexity estimator heuristics."""
from __future__ import annotations

from app.domain.models import NodeContext
from app.services.complexity import ComplexityEstimate, ComplexityEstimator


class TestComplexityEstimate:
    def test_is_complex_threshold(self):
        assert ComplexityEstimate(0.7, 4, "strong", "x").is_complex
        assert ComplexityEstimate(0.6, 3, "standard", "x").is_complex
        assert not ComplexityEstimate(0.5, 3, "standard", "x").is_complex
        assert not ComplexityEstimate(0.1, 2, "fast", "x").is_complex


class TestComplexityEstimator:
    def setup_method(self):
        self.est = ComplexityEstimator()

    def test_simple_task_scores_low(self):
        r = self.est.estimate("rename variable x to y")
        assert r.score < 0.4
        assert r.model_tier == "fast"

    def test_complex_task_scores_high(self):
        r = self.est.estimate(
            "architect a distributed system with authentication, "
            "integrate multiple services, and optimize for scalability"
        )
        assert r.score >= 0.6
        assert r.model_tier in ("standard", "strong")

    def test_depth_reduces_complexity(self):
        obj = "implement a basic sorting algorithm"
        shallow = self.est.estimate(obj, depth=0)
        deep = self.est.estimate(obj, depth=4)
        assert deep.score < shallow.score

    def test_checker_feedback_increases_complexity(self):
        obj = "implement parser"
        ctx_no_feedback = NodeContext(root_objective=obj)
        ctx_with_feedback = NodeContext(
            root_objective=obj,
            checker_feedback="Previous attempt failed: missing edge cases",
        )
        r1 = self.est.estimate(obj, context=ctx_no_feedback)
        r2 = self.est.estimate(obj, context=ctx_with_feedback)
        assert r2.score > r1.score

    def test_multi_step_markers_increase_score(self):
        single = self.est.estimate("create a function")
        multi = self.est.estimate("create a function and then test it and also document it")
        assert multi.score > single.score

    def test_score_clamped_0_to_1(self):
        r = self.est.estimate("rename x", depth=10)
        assert 0.0 <= r.score <= 1.0
        r2 = self.est.estimate(
            "architect distributed concurrent scalable system "
            "with security and authentication and integration and optimization"
        )
        assert 0.0 <= r2.score <= 1.0

    def test_tier_selection(self):
        assert ComplexityEstimator._select_tier(0.8) == "strong"
        assert ComplexityEstimator._select_tier(0.5) == "standard"
        assert ComplexityEstimator._select_tier(0.2) == "fast"

    def test_depth_suggestion(self):
        assert ComplexityEstimator._suggest_depth(0.9) == 6
        assert ComplexityEstimator._suggest_depth(0.6) == 4
        assert ComplexityEstimator._suggest_depth(0.4) == 3
        assert ComplexityEstimator._suggest_depth(0.2) == 2
