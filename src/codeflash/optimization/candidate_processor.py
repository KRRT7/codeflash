from __future__ import annotations

import concurrent.futures
import queue
from collections.abc import Callable

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.config_consts import EffortKeys, get_effort_value
from codeflash.models.candidate_evaluation_context import CandidateEvaluationContext
from codeflash.models.domain import (
    OptimizedCandidate,
)


MIN_CORRECT_CANDIDATES = 1


class CandidateNode:
    __slots__ = ("candidate", "children", "parent")

    def __init__(self, candidate: OptimizedCandidate | None) -> None:
        self.candidate = candidate
        self.parent: CandidateNode | None = None
        self.children: list[CandidateNode] = []

    def is_leaf(self) -> bool:
        return not self.children

    def path_to_root(self) -> list[OptimizedCandidate]:
        path = []
        node: CandidateNode | None = self
        while node:
            path.append(node.candidate)
            node = node.parent
        return path[::-1]


class CandidateForest:
    def __init__(self) -> None:
        self.nodes: dict[str, CandidateNode] = {}

    def add(self, candidate: OptimizedCandidate) -> CandidateNode:
        cid = candidate.optimization_id
        pid = candidate.parent_id

        node = self.nodes.get(cid)
        if node is None:
            node = CandidateNode(candidate)
            self.nodes[cid] = node

        if pid is not None:
            parent = self.nodes.get(pid)
            if parent is None:
                parent = CandidateNode(candidate=None)
                self.nodes[pid] = parent

            node.parent = parent
            parent.children.append(node)

        return node

    def get_node(self, cid: str) -> CandidateNode | None:
        return self.nodes.get(cid)

    def get_root_candidates(self) -> list[CandidateNode]:
        return [
            node
            for node in self.nodes.values()
            if node.parent is None and node.candidate is not None
        ]


class CandidateProcessor:
    """Handles candidate processing using a queue-based approach."""

    def __init__(
        self,
        initial_candidates: list[OptimizedCandidate],
        future_line_profile_results: concurrent.futures.Future,
        eval_ctx: CandidateEvaluationContext,
        effort: str,
        original_markdown_code: str,
        future_all_refinements: list[concurrent.futures.Future],
        future_all_code_repair: list[concurrent.futures.Future],
        future_adaptive_optimizations: list[concurrent.futures.Future],
    ) -> None:
        self.candidate_queue: queue.Queue[OptimizedCandidate] = queue.Queue()
        self.forest = CandidateForest()
        self.line_profiler_done = False
        self.refinement_done = False
        self.eval_ctx = eval_ctx
        self.effort = effort
        self.candidate_len = len(initial_candidates)
        self.refinement_calls_count = 0
        self.original_markdown_code = original_markdown_code

        for candidate in initial_candidates:
            self.forest.add(candidate)
            self.candidate_queue.put(candidate)

        self.future_line_profile_results = future_line_profile_results
        self.future_all_refinements = future_all_refinements
        self.future_all_code_repair = future_all_code_repair
        self.future_adaptive_optimizations = future_adaptive_optimizations

    def get_total_llm_calls(self) -> int:
        return self.refinement_calls_count

    def is_done(self) -> bool:
        return (
            self.line_profiler_done
            and self.refinement_done
            and len(self.future_all_code_repair) == 0
            and len(self.future_adaptive_optimizations) == 0
            and self.candidate_queue.empty()
        )

    def get_next_candidate(self) -> CandidateNode | None:
        try:
            return self.forest.get_node(
                self.candidate_queue.get_nowait().optimization_id
            )
        except queue.Empty:
            return self._handle_empty_queue()

    def _handle_empty_queue(self) -> CandidateNode | None:
        if not self.line_profiler_done:
            return self._process_candidates(
                [self.future_line_profile_results],
                "all candidates processed, await candidates from line profiler",
                "Added results from line profiler to candidates, total candidates now: {1}",
                lambda: setattr(self, "line_profiler_done", True),
            )
        if len(self.future_all_code_repair) > 0:
            return self._process_candidates(
                self.future_all_code_repair,
                "Repairing {0} candidates",
                "Added {0} candidates from repair, total candidates now: {1}",
                lambda: self.future_all_code_repair.clear(),
            )
        if self.line_profiler_done and not self.refinement_done:
            return self._process_candidates(
                self.future_all_refinements,
                "Refining generated code for improved quality and performance...",
                "Added {0} candidates from refinement, total candidates now: {1}",
                lambda: setattr(self, "refinement_done", True),
                filter_candidates_func=self._filter_refined_candidates,
            )
        if len(self.future_adaptive_optimizations) > 0:
            return self._process_candidates(
                self.future_adaptive_optimizations,
                "Applying adaptive optimizations to {0} candidates",
                "Added {0} candidates from adaptive optimization, total candidates now: {1}",
                lambda: self.future_adaptive_optimizations.clear(),
            )
        return None

    def _process_candidates(
        self,
        future_candidates: list[concurrent.futures.Future],
        loading_msg: str,
        success_msg: str,
        callback: Callable[[], None],
        filter_candidates_func: Callable[
            [list[OptimizedCandidate]], list[OptimizedCandidate]
        ]
        | None = None,
    ) -> CandidateNode | None:
        if len(future_candidates) == 0:
            return None
        logger.info(loading_msg.format(len(future_candidates)))
        concurrent.futures.wait(future_candidates)
        candidates: list[OptimizedCandidate] = []
        for future_c in future_candidates:
            candidate_result = future_c.result()
            if not candidate_result:
                continue
            if isinstance(candidate_result, list):
                candidates.extend(candidate_result)
            else:
                candidates.append(candidate_result)

        candidates = (
            filter_candidates_func(candidates) if filter_candidates_func else candidates
        )
        for candidate in candidates:
            self.forest.add(candidate)
            self.candidate_queue.put(candidate)
            self.candidate_len += 1

        if candidates:
            logger.info(success_msg.format(len(candidates), self.candidate_len))

        callback()
        return self.get_next_candidate()

    def _filter_refined_candidates(
        self, candidates: list[OptimizedCandidate]
    ) -> list[OptimizedCandidate]:
        self.refinement_calls_count += len(candidates)
        top_n_candidates = int(
            min(
                int(
                    get_effort_value(
                        EffortKeys.TOP_VALID_CANDIDATES_FOR_REFINEMENT, self.effort
                    )
                ),
                len(candidates),
            )
        )

        if len(candidates) == top_n_candidates:
            return candidates

        diff_lens_list = []
        runtimes_list = []
        for c in candidates:
            parent_id = c.parent_id
            parent_candidate_node = self.forest.get_node(parent_id)
            parent_diff = (
                len(parent_candidate_node.candidate.source_code.flat)
                if parent_candidate_node
                else 0
            )
            diff_lens_list.append(abs(len(c.source_code.flat) - parent_diff))
            parent_runtime = (
                self.eval_ctx.get_optimized_runtime(parent_id) or 0
                if parent_candidate_node
                else 0
            )
            runtimes_list.append(parent_runtime)

        normalized_diff_lens = _normalize(diff_lens_list)
        normalized_runtimes = _normalize(runtimes_list)

        diff_weight = get_effort_value(
            EffortKeys.REFINEMENT_SELECTION_DIFF_WEIGHT, self.effort
        )
        runtime_weight = 1.0 - diff_weight

        combined_scores = [
            (diff_weight * diff_score + runtime_weight * runtime_score)
            for diff_score, runtime_score in zip(
                normalized_diff_lens, normalized_runtimes
            )
        ]

        ranked = sorted(zip(combined_scores, candidates), key=lambda x: x[0])
        return [c for _, c in ranked[:top_n_candidates]]


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    mx = max(abs(v) for v in values)
    if mx == 0:
        return [0.0] * len(values)
    return [v / mx for v in values]
