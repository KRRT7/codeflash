from __future__ import annotations

from enum import Enum, IntEnum
from typing import NamedTuple

from pydantic.dataclasses import dataclass


class CoverReturnCode(IntEnum):
    DID_NOT_RUN = -1
    NO_DIFFERENCES = 0
    COUNTER_EXAMPLES = 1
    ERROR = 2


class TestDiffScope(str, Enum):
    RETURN_VALUE = "return_value"
    STDOUT = "stdout"
    DID_PASS = "did_pass"


@dataclass
class TestDiff:
    scope: TestDiffScope
    original_pass: bool
    candidate_pass: bool
    original_value: str | None = None
    candidate_value: str | None = None
    test_src_code: str | None = None
    candidate_pytest_error: str | None = None
    original_pytest_error: str | None = None


@dataclass(frozen=True)
class AIServiceRefinerRequest:
    optimization_id: str
    original_source_code: str
    read_only_dependency_code: str
    original_code_runtime: int
    optimized_source_code: str
    optimized_explanation: str
    optimized_code_runtime: int
    speedup: str
    trace_id: str
    original_line_profiler_results: str
    optimized_line_profiler_results: str
    function_references: str | None = None
    call_sequence: int | None = None


class OptimizedCandidateSource(str, Enum):
    OPTIMIZE = "OPTIMIZE"
    OPTIMIZE_LP = "OPTIMIZE_LP"
    REFINE = "REFINE"
    REPAIR = "REPAIR"
    ADAPTIVE = "ADAPTIVE"


@dataclass(frozen=True)
class AdaptiveOptimizedCandidate:
    optimization_id: str
    source_code: str
    explanation: str
    source: OptimizedCandidateSource
    speedup: str


@dataclass(frozen=True)
class AIServiceAdaptiveOptimizeRequest:
    trace_id: str
    original_source_code: str
    candidates: list[AdaptiveOptimizedCandidate]


@dataclass(frozen=True)
class AIServiceCodeRepairRequest:
    optimization_id: str
    original_source_code: str
    modified_source_code: str
    trace_id: str
    test_diffs: list[TestDiff]


class OptimizationReviewResult(NamedTuple):
    review: str
    explanation: str
