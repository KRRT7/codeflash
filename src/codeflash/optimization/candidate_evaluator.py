from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from codeflash.cli_cmds.logging_config import logger, rule
from codeflash.code_utils.config_consts import EffortKeys, get_effort_value
from codeflash.code_utils.deduplicate_code import normalize_code
from codeflash.code_utils.diff_utils import (
    create_rank_dictionary_compact,
    diff_length,
    unified_diff_strings,
)
from codeflash.models.domain import (
    BestOptimization,
    CandidateEvaluationContext,
    ExperimentMetadata,
    OptimizedCandidate,
)
from codeflash.optimization.candidate_processor import CandidateProcessor
from codeflash.result.critic import performance_gain

if TYPE_CHECKING:
    from codeflash.api.aiservice import AiServiceClient
    from codeflash.discovery.functions_to_optimize import FunctionToOptimize
    from codeflash.models.domain import CodeOptimizationContext, OriginalCodeBaseline


def select_best_optimization(
    eval_ctx: CandidateEvaluationContext,
    code_context: CodeOptimizationContext,
    original_code_baseline: OriginalCodeBaseline,
    ai_service_client: AiServiceClient,
    trace_id: str,
    function_references: str,
    executor: concurrent.futures.ThreadPoolExecutor,
) -> BestOptimization | None:
    if not eval_ctx.valid_optimizations:
        return None

    valid_candidates_with_shorter_code = []
    diff_lens_list = []
    speedups_list = []
    optimization_ids = []
    diff_strs = []
    runtimes_list = []

    for valid_opt in eval_ctx.valid_optimizations:
        valid_opt_normalized_code = normalize_code(
            valid_opt.candidate.source_code.flat.strip()
        )
        new_candidate_with_shorter_code = OptimizedCandidate(
            source_code=eval_ctx.ast_code_to_id[valid_opt_normalized_code][
                "shorter_source_code"
            ],
            optimization_id=valid_opt.candidate.optimization_id,
            explanation=valid_opt.candidate.explanation,
            source=valid_opt.candidate.source,
            parent_id=valid_opt.candidate.parent_id,
        )
        new_best_opt = BestOptimization(
            candidate=new_candidate_with_shorter_code,
            helper_functions=valid_opt.helper_functions,
            code_context=valid_opt.code_context,
            runtime=valid_opt.runtime,
            line_profiler_test_results=valid_opt.line_profiler_test_results,
            winning_behavior_test_results=valid_opt.winning_behavior_test_results,
            replay_performance_gain=valid_opt.replay_performance_gain,
            winning_benchmarking_test_results=valid_opt.winning_benchmarking_test_results,
            winning_replay_benchmarking_test_results=valid_opt.winning_replay_benchmarking_test_results,
            async_throughput=valid_opt.async_throughput,
        )
        valid_candidates_with_shorter_code.append(new_best_opt)
        diff_lens_list.append(
            diff_length(
                new_best_opt.candidate.source_code.flat,
                code_context.read_writable_code.flat,
            )
        )
        diff_strs.append(
            unified_diff_strings(
                code_context.read_writable_code.flat,
                new_best_opt.candidate.source_code.flat,
            )
        )
        speedups_list.append(
            1
            + performance_gain(
                original_runtime_ns=original_code_baseline.runtime,
                optimized_runtime_ns=new_best_opt.runtime,
            )
        )
        optimization_ids.append(new_best_opt.candidate.optimization_id)
        runtimes_list.append(new_best_opt.runtime)

    if len(optimization_ids) > 1:
        future_ranking = executor.submit(
            ai_service_client.generate_ranking,
            diffs=diff_strs,
            optimization_ids=optimization_ids,
            speedups=speedups_list,
            trace_id=trace_id,
            function_references=function_references,
        )
        concurrent.futures.wait([future_ranking])
        ranking = future_ranking.result()
        if ranking:
            min_key = ranking[0]
        else:
            diff_lens_ranking = create_rank_dictionary_compact(diff_lens_list)
            runtimes_ranking = create_rank_dictionary_compact(runtimes_list)
            overall_ranking = {
                key: diff_lens_ranking[key] + runtimes_ranking[key]
                for key in diff_lens_ranking
            }
            min_key = min(overall_ranking, key=overall_ranking.get)
    elif len(optimization_ids) == 1:
        min_key = 0
    else:
        return None

    return valid_candidates_with_shorter_code[min_key]


def log_evaluation_results(
    eval_ctx: CandidateEvaluationContext,
    best_optimization: BestOptimization,
    original_code_baseline: OriginalCodeBaseline,
    ai_service_client: AiServiceClient,
    trace_id: str,
) -> None:
    ai_service_client.log_results(
        function_trace_id=trace_id,
        speedup_ratio=eval_ctx.speedup_ratios,
        original_runtime=original_code_baseline.runtime,
        optimized_runtime=eval_ctx.optimized_runtimes,
        is_correct=eval_ctx.is_correct,
        optimized_line_profiler_results=eval_ctx.optimized_line_profiler_results,
        optimizations_post=eval_ctx.optimizations_post,
        metadata={"best_optimization_id": best_optimization.candidate.optimization_id},
    )


def determine_best_candidate(
    function_to_optimize: FunctionToOptimize,
    executor: concurrent.futures.ThreadPoolExecutor,
    aiservice_client: AiServiceClient | None,
    local_aiservice_client: AiServiceClient | None,
    future_all_refinements: list[concurrent.futures.Future],
    future_all_code_repair: list[concurrent.futures.Future],
    future_adaptive_optimizations: list[concurrent.futures.Future],
    experiment_id: str | None,
    effort: str,
    function_to_optimize_source_code: str,
    function_to_optimize_file_path: Path,
    code_context: CodeOptimizationContext,
    original_code_baseline: OriginalCodeBaseline,
    original_helper_code: dict[Path, str],
    file_path_to_helper_classes: dict[Path, set[str]],
    exp_type: str,
    function_references: str,
    candidates: list[OptimizedCandidate],
    get_trace_id: Callable[[str], str],
    process_single_candidate: Callable[
        ...,
        BestOptimization | None,
    ],
    write_code_and_helpers: Callable[
        [str, dict[Path, str], Path],
        None,
    ],
) -> BestOptimization | None:
    qualified_name = function_to_optimize.qualified_name
    logger.info(
        f"Determining best optimization candidate (out of {len(candidates)}) for "
        f"{qualified_name}…"
    )
    rule()

    eval_ctx = CandidateEvaluationContext()

    future_all_refinements.clear()
    future_all_code_repair.clear()
    future_adaptive_optimizations.clear()

    ai_service_client = (
        aiservice_client if exp_type == "EXP0" else local_aiservice_client
    )
    assert ai_service_client is not None, (
        "AI service client must be set for optimization"
    )

    future_line_profile_results = executor.submit(
        ai_service_client.optimize_python_code_line_profiler,
        source_code=code_context.read_writable_code.markdown,
        dependency_code=code_context.read_only_context_code,
        trace_id=get_trace_id(exp_type),
        line_profiler_results=original_code_baseline.line_profile_results["str_out"],
        n_candidates=get_effort_value(EffortKeys.N_OPTIMIZER_LP_CANDIDATES, effort),
        experiment_metadata=ExperimentMetadata(
            id=experiment_id,
            group="control" if exp_type == "EXP0" else "experiment",
        )
        if experiment_id
        else None,
    )

    processor = CandidateProcessor(
        candidates,
        future_line_profile_results,
        eval_ctx,
        effort,
        code_context.read_writable_code.markdown,
        future_all_refinements,
        future_all_code_repair,
        future_adaptive_optimizations,
    )
    candidate_index = 0

    while not processor.is_done():
        candidate_node = processor.get_next_candidate()
        if candidate_node is None:
            logger.debug("everything done, exiting")
            break

        try:
            candidate_index += 1
            process_single_candidate(
                candidate_node=candidate_node,
                candidate_index=candidate_index,
                total_candidates=processor.candidate_len,
                code_context=code_context,
                original_code_baseline=original_code_baseline,
                original_helper_code=original_helper_code,
                file_path_to_helper_classes=file_path_to_helper_classes,
                eval_ctx=eval_ctx,
                exp_type=exp_type,
                function_references=function_references,
            )
        except KeyboardInterrupt as e:
            logger.exception(f"Optimization interrupted: {e}")
            raise
        finally:
            write_code_and_helpers(
                function_to_optimize_source_code,
                original_helper_code,
                function_to_optimize_file_path,
            )

    best_optimization_result = select_best_optimization(
        eval_ctx=eval_ctx,
        code_context=code_context,
        original_code_baseline=original_code_baseline,
        ai_service_client=ai_service_client,
        trace_id=get_trace_id(exp_type),
        function_references=function_references,
        executor=executor,
    )

    if best_optimization_result:
        log_evaluation_results(
            eval_ctx=eval_ctx,
            best_optimization=best_optimization_result,
            original_code_baseline=original_code_baseline,
            ai_service_client=ai_service_client,
            trace_id=get_trace_id(exp_type),
        )

    return best_optimization_result
