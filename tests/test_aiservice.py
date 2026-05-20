from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from codeflash.models.api import (
    AIServiceAdaptiveOptimizeRequest,
    AIServiceCodeRepairRequest,
    AIServiceRefinerRequest,
    AdaptiveOptimizedCandidate,
    OptimizedCandidateSource,
)

OPTIMIZATIONS_RESPONSE = {
    "optimizations": [
        {
            "source_code": "```python:test.py\nx = 1\n```",
            "explanation": "faster",
            "optimization_id": "opt-1",
            "parent_id": None,
            "model": "gpt-4",
        }
    ]
}

EMPTY_RESULTS = (
    None,
    None,
)


@pytest.fixture
def client() -> MagicMock:
    from codeflash.api.aiservice import AiServiceClient

    with patch(
        "codeflash.api.aiservice.get_codeflash_api_key", return_value="cf-test-key"
    ):
        c = AiServiceClient()
    c.base_url = "https://test.api"
    c.is_local = False
    c.timeout = 90
    return c


class TestInit:
    def test_default_base_url(self) -> None:
        with (
            patch("codeflash.api.aiservice.get_codeflash_api_key", return_value="key"),
            patch.dict("os.environ", clear=True),
        ):
            from codeflash.api.aiservice import AiServiceClient

            c = AiServiceClient()
            assert c.base_url == "https://app.codeflash.ai"
            assert c.timeout == 90

    def test_local_base_url(self) -> None:
        with (
            patch("codeflash.api.aiservice.get_codeflash_api_key", return_value="key"),
            patch.dict("os.environ", {"CODEFLASH_AIS_SERVER": "local"}),
        ):
            from codeflash.api.aiservice import AiServiceClient

            c = AiServiceClient()
            assert c.base_url == "http://localhost:8000"
            assert c.timeout is None

    def test_get_next_sequence(self, client: MagicMock) -> None:
        assert client.get_next_sequence() == 1
        assert client.get_next_sequence() == 2


class TestMakeAiServiceRequest:
    def test_post_request(self, client: MagicMock) -> None:
        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            response = client.make_ai_service_request(
                "/optimize", payload={"key": "val"}
            )

            mock_post.assert_called_once()
            url = mock_post.call_args[0][0]
            assert url == "https://test.api/ai/optimize"
            assert json.loads(mock_post.call_args[1]["data"]) == {"key": "val"}
            assert (
                mock_post.call_args[1]["headers"]["Authorization"]
                == "Bearer cf-test-key"
            )
            assert response.status_code == 200


class TestGetValidCandidates:
    def test_valid(self, client: MagicMock) -> None:
        candidates = client._get_valid_candidates(
            OPTIMIZATIONS_RESPONSE["optimizations"], OptimizedCandidateSource.OPTIMIZE
        )
        assert len(candidates) == 1
        assert candidates[0].optimization_id == "opt-1"
        assert candidates[0].source == OptimizedCandidateSource.OPTIMIZE

    def test_skips_invalid_code(self, client: MagicMock) -> None:
        candidates = client._get_valid_candidates(
            [
                {
                    "source_code": "```python:test.py\n```",
                    "explanation": "empty",
                    "optimization_id": "opt-empty",
                }
            ],
            OptimizedCandidateSource.OPTIMIZE,
        )
        assert len(candidates) == 0


class TestOptimizePythonCode:
    def test_success(self, client: MagicMock) -> None:
        with patch.object(client, "make_ai_service_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = OPTIMIZATIONS_RESPONSE
            mock_request.return_value = mock_response

            result = client.optimize_python_code("def foo(): pass", "", "trace-1")
            assert len(result) == 1
            assert result[0].optimization_id == "opt-1"

    def test_request_exception_returns_empty(self, client: MagicMock) -> None:
        with patch.object(
            client,
            "make_ai_service_request",
            side_effect=requests.exceptions.RequestException,
        ):
            result = client.optimize_python_code("def foo(): pass", "", "trace-1")
            assert result == []

    def test_error_status_returns_empty(self, client: MagicMock) -> None:
        with patch.object(client, "make_ai_service_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_response.json.side_effect = ValueError
            mock_request.return_value = mock_response

            result = client.optimize_python_code("def foo(): pass", "", "trace-1")
            assert result == []


class TestOptimizePythonCodeLineProfiler:
    def test_empty_profile_returns_empty(self, client: MagicMock) -> None:
        result = client.optimize_python_code_line_profiler("code", "", "trace-1", "", 5)
        assert result == []

    def test_success(self, client: MagicMock) -> None:
        with patch.object(client, "make_ai_service_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = OPTIMIZATIONS_RESPONSE
            mock_request.return_value = mock_response

            result = client.optimize_python_code_line_profiler(
                "code", "", "trace-1", "profile data", 5
            )
            assert len(result) == 1


class TestOptimizePythonCodeRefinement:
    def test_success(self, client: MagicMock) -> None:
        with patch.object(client, "make_ai_service_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "refinements": OPTIMIZATIONS_RESPONSE["optimizations"]
            }
            mock_request.return_value = mock_response

            req = [
                AIServiceRefinerRequest(
                    optimization_id="opt-1",
                    original_source_code="original",
                    read_only_dependency_code="",
                    original_code_runtime=100,
                    optimized_source_code="optimized",
                    optimized_explanation="exp",
                    optimized_code_runtime=50,
                    speedup="2x",
                    trace_id="trace-1",
                    original_line_profiler_results="",
                    optimized_line_profiler_results="",
                )
            ]
            result = client.optimize_python_code_refinement(req)
            assert len(result) == 1

    def test_error_returns_empty(self, client: MagicMock) -> None:
        with patch.object(
            client,
            "make_ai_service_request",
            side_effect=requests.exceptions.RequestException,
        ):
            result = client.optimize_python_code_refinement([])
            assert result == []


class TestAdaptiveOptimize:
    def test_success(self, client: MagicMock) -> None:
        with patch.object(client, "make_ai_service_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = OPTIMIZATIONS_RESPONSE["optimizations"][0]
            mock_request.return_value = mock_response

            req = AIServiceAdaptiveOptimizeRequest(
                trace_id="trace-1",
                original_source_code="original",
                candidates=[
                    AdaptiveOptimizedCandidate(
                        optimization_id="opt-1",
                        source_code="code",
                        explanation="exp",
                        source=OptimizedCandidateSource.OPTIMIZE,
                        speedup="2x",
                    )
                ],
            )
            result = client.adaptive_optimize(req)
            assert result is not None
            assert result.optimization_id == "opt-1"

    def test_error_returns_none(self, client: MagicMock) -> None:
        with patch.object(
            client,
            "make_ai_service_request",
            side_effect=requests.exceptions.RequestException,
        ):
            result = client.adaptive_optimize(MagicMock())
            assert result is None


class TestCodeRepair:
    def test_success(self, client: MagicMock) -> None:
        with patch.object(client, "make_ai_service_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = OPTIMIZATIONS_RESPONSE["optimizations"][0]
            mock_request.return_value = mock_response

            from codeflash.models.api import TestDiff, TestDiffScope

            req = AIServiceCodeRepairRequest(
                optimization_id="opt-1",
                original_source_code="original",
                modified_source_code="modified",
                trace_id="trace-1",
                test_diffs=[
                    TestDiff(
                        scope=TestDiffScope.RETURN_VALUE,
                        original_pass=True,
                        candidate_pass=False,
                    )
                ],
            )
            result = client.code_repair(req)
            assert result is not None
            assert result.optimization_id == "opt-1"

    def test_error_returns_none(self, client: MagicMock) -> None:
        with patch.object(
            client,
            "make_ai_service_request",
            side_effect=requests.exceptions.RequestException,
        ):
            result = client.code_repair(MagicMock())
            assert result is None


class TestGetReview:
    def test_success(self, client: MagicMock) -> None:
        with patch.object(client, "make_ai_service_request") as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "review": "high",
                "explanation": "Great optimization",
            }
            mock_request.return_value = mock_response

            from codeflash.models.api import OptimizationReviewResult

            mock_explanation = MagicMock()
            mock_explanation.best_runtime_ns = 100
            mock_explanation.original_runtime_ns = 200
            mock_explanation.function_name = "foo"
            mock_explanation.file_path = "/tmp/test.py"
            mock_explanation.speedup_x = "2x"
            mock_explanation.speedup_pct = "50%"
            mock_explanation.winning_behavior_test_results = MagicMock()
            mock_explanation.winning_benchmarking_test_results = MagicMock()
            mock_explanation.winning_benchmarking_test_results.number_of_loops.return_value = 5
            mock_explanation.benchmark_details = None

            result = client.get_optimization_review(
                {"/tmp/test.py": "code"},
                {"/tmp/test.py": "opt_code"},
                mock_explanation,
                "existing_tests",
                "generated_tests",
                "trace-1",
                "coverage: 90%",
                "",
                "",
                "",
            )
            assert isinstance(result, OptimizationReviewResult)
            assert result.review == "high"
