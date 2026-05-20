from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from codeflash.cli_cmds.logging_config import logger, rule

if TYPE_CHECKING:
    from codeflash.benchmarking.function_ranker import FunctionRanker
    from codeflash.discovery.functions_to_optimize import FunctionToOptimize


def display_global_ranking(
    globally_ranked: list[tuple[Path, FunctionToOptimize]],
    ranker: FunctionRanker,
    show_top_n: int = 15,
) -> None:
    if not globally_ranked:
        return

    display_count = min(show_top_n, len(globally_ranked))

    print()
    print(f"Function Ranking (Top {display_count} of {len(globally_ranked)})")
    print(
        f"{'Priority':>8} {'Function':<40} {'File':<25} {'Addressable Time':>12} {'Impact':>8}"
    )
    print("-" * 100)

    for i, (file_path, func) in enumerate(globally_ranked[:display_count], 1):
        addressable_time = ranker.get_function_addressable_time(func)

        func_name = func.qualified_name
        if len(func_name) > 38:
            func_name = func_name[:35] + "..."

        file_name = file_path.name
        if len(file_name) > 23:
            file_name = "..." + file_name[-20:]

        if addressable_time >= 1e9:
            time_display = f"{addressable_time / 1e9:.2f}s"
        elif addressable_time >= 1e6:
            time_display = f"{addressable_time / 1e6:.1f}ms"
        elif addressable_time >= 1e3:
            time_display = f"{addressable_time / 1e3:.1f}µs"
        else:
            time_display = f"{addressable_time:.0f}ns"

        if i <= 5:
            impact = "🔥"
        elif i <= 10:
            impact = "⚡"
        else:
            impact = "💡"

        print(
            f"{f'#{i}':>8} {func_name:<40} {file_name:<25} {time_display:>12} {impact:>8}"
        )

    if len(globally_ranked) > display_count:
        print(f"... and {len(globally_ranked) - display_count} more functions")


def rank_all_functions_globally(
    file_to_funcs_to_optimize: dict[Path, list[FunctionToOptimize]],
    trace_file_path: Path | None,
) -> list[tuple[Path, FunctionToOptimize]]:
    all_functions: list[tuple[Path, FunctionToOptimize]] = []
    for file_path, functions in file_to_funcs_to_optimize.items():
        all_functions.extend((file_path, func) for func in functions)

    if not trace_file_path or not trace_file_path.exists():
        logger.debug("No trace file available, using original function order")
        return all_functions

    try:
        from codeflash.benchmarking.function_ranker import FunctionRanker

        rule()
        logger.info("loading|Ranking functions globally by performance impact...")
        rule()
        ranker = FunctionRanker(trace_file_path)

        functions_only = [func for _, func in all_functions]
        ranked_functions = ranker.rank_functions(functions_only)

        func_to_file_map = {}
        for file_path, func in all_functions:
            key: tuple[Path, str, int | None] = (
                func.file_path,
                func.qualified_name,
                func.starting_line,
            )
            func_to_file_map[key] = file_path
        globally_ranked = []
        for func in ranked_functions:
            key = (func.file_path, func.qualified_name, func.starting_line)
            file_path = func_to_file_map.get(key)
            if file_path:
                globally_ranked.append((file_path, func))

        rule()
        logger.info(
            f"Globally ranked {len(ranked_functions)} functions by addressable time "
            f"(filtered {len(functions_only) - len(ranked_functions)} low-importance functions)"
        )

        display_global_ranking(globally_ranked, ranker)
        rule()

    except Exception as e:
        logger.warning(f"Could not perform global ranking: {e}")
        logger.debug("Falling back to original function order")
        return all_functions
    else:
        return globally_ranked
