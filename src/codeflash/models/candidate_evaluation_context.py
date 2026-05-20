from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field
from pydantic.dataclasses import dataclass

from codeflash.code_utils.diff_utils import diff_length

if TYPE_CHECKING:
    from codeflash.models.domain import CodeOptimizationContext, OptimizedCandidate


@dataclass
class CandidateEvaluationContext:
    speedup_ratios: dict[str, float | None] = Field(default_factory=dict)
    optimized_runtimes: dict[str, float | None] = Field(default_factory=dict)
    is_correct: dict[str, bool] = Field(default_factory=dict)
    optimized_line_profiler_results: dict[str, str] = Field(default_factory=dict)
    ast_code_to_id: dict = Field(default_factory=dict)
    optimizations_post: dict[str, str] = Field(default_factory=dict)
    valid_optimizations: list = Field(default_factory=list)

    def record_failed_candidate(self, optimization_id: str) -> None:
        self.optimized_runtimes[optimization_id] = None
        self.is_correct[optimization_id] = False
        self.speedup_ratios[optimization_id] = None

    def record_successful_candidate(
        self, optimization_id: str, runtime: float, speedup: float
    ) -> None:
        self.optimized_runtimes[optimization_id] = runtime
        self.is_correct[optimization_id] = True
        self.speedup_ratios[optimization_id] = speedup

    def record_line_profiler_result(self, optimization_id: str, result: str) -> None:
        self.optimized_line_profiler_results[optimization_id] = result

    def handle_duplicate_candidate(
        self,
        candidate: OptimizedCandidate,
        normalized_code: str,
        code_context: CodeOptimizationContext,
    ) -> None:
        past_opt_id = self.ast_code_to_id[normalized_code]["optimization_id"]
        self.speedup_ratios[candidate.optimization_id] = self.speedup_ratios[
            past_opt_id
        ]
        self.is_correct[candidate.optimization_id] = self.is_correct[past_opt_id]
        self.optimized_runtimes[candidate.optimization_id] = self.optimized_runtimes[
            past_opt_id
        ]
        if past_opt_id in self.optimized_line_profiler_results:
            self.optimized_line_profiler_results[candidate.optimization_id] = (
                self.optimized_line_profiler_results[past_opt_id]
            )
        self.optimizations_post[candidate.optimization_id] = self.ast_code_to_id[
            normalized_code
        ]["shorter_source_code"].markdown
        self.optimizations_post[past_opt_id] = self.ast_code_to_id[normalized_code][
            "shorter_source_code"
        ].markdown
        new_diff_len = diff_length(
            candidate.source_code.flat, code_context.read_writable_code.flat
        )
        if new_diff_len < self.ast_code_to_id[normalized_code]["diff_len"]:
            self.ast_code_to_id[normalized_code]["shorter_source_code"] = (
                candidate.source_code
            )
            self.ast_code_to_id[normalized_code]["diff_len"] = new_diff_len

    def register_new_candidate(
        self,
        normalized_code: str,
        candidate: OptimizedCandidate,
        code_context: CodeOptimizationContext,
    ) -> None:
        self.ast_code_to_id[normalized_code] = {
            "optimization_id": candidate.optimization_id,
            "shorter_source_code": candidate.source_code,
            "diff_len": diff_length(
                candidate.source_code.flat, code_context.read_writable_code.flat
            ),
        }

    def get_speedup_ratio(self, optimization_id: str) -> float | None:
        return self.speedup_ratios.get(optimization_id)

    def get_optimized_runtime(self, optimization_id: str) -> float | None:
        return self.optimized_runtimes.get(optimization_id)
