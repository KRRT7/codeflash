from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from codeflash.cli_cmds.logging_config import rule
from codeflash.code_utils.cleanup import cleanup_paths, restore_conftest
from codeflash.danom import Err, Ok
from codeflash.models.domain import OriginalCodeBaseline
from codeflash.result.critic import coverage_critic, quantity_of_tests_critic

if TYPE_CHECKING:
    from codeflash.danom import Result
    from codeflash.discovery.functions_to_optimize import FunctionToOptimize
    from codeflash.models.config import AppConfig
    from codeflash.models.domain import (
        CodeOptimizationContext,
        FunctionCalledInTest,
    )


def setup_and_establish_baseline(
    function_to_optimize: FunctionToOptimize,
    function_to_tests: dict[str, set[FunctionCalledInTest]],
    config: AppConfig,
    code_context: CodeOptimizationContext,
    original_helper_code: dict[Path, str],
    function_to_concolic_tests: dict[str, set[FunctionCalledInTest]],
    generated_test_paths: list[Path],
    generated_perf_test_paths: list[Path],
    instrumented_unittests_created_for_function: set[Path],
    original_conftest_content: Any,
    establish_baseline: Callable[
        [CodeOptimizationContext, dict[Path, str], dict[Path, set[str]]],
        Result[tuple[OriginalCodeBaseline, list[str]], str],
    ],
) -> Result[
    tuple[
        str,
        dict[str, set[FunctionCalledInTest]],
        OriginalCodeBaseline,
        list[str],
        dict[Path, set[str]],
    ],
    str,
]:
    function_to_optimize_qualified_name = function_to_optimize.qualified_name
    function_to_all_tests = {
        key: function_to_tests.get(key, set())
        | function_to_concolic_tests.get(key, set())
        for key in set(function_to_tests) | set(function_to_concolic_tests)
    }

    file_path_to_helper_classes = defaultdict(set)
    for function_source in code_context.helper_functions:
        if (
            function_source.qualified_name != function_to_optimize.qualified_name
            and "." in function_source.qualified_name
        ):
            file_path_to_helper_classes[function_source.file_path].add(
                function_source.qualified_name.split(".")[0]
            )

    baseline_result = establish_baseline(
        code_context, original_helper_code, file_path_to_helper_classes
    )

    rule()
    paths_to_cleanup = (
        generated_test_paths
        + generated_perf_test_paths
        + list(instrumented_unittests_created_for_function)
    )

    if not baseline_result.is_ok():
        if config.override_fixtures:
            restore_conftest(original_conftest_content)
        cleanup_paths(paths_to_cleanup)
        return baseline_result  # type: ignore[return-value]

    original_code_baseline, test_functions_to_remove = baseline_result.unwrap()
    if isinstance(original_code_baseline, OriginalCodeBaseline) and (
        not coverage_critic(original_code_baseline.coverage_results)
        or not quantity_of_tests_critic(original_code_baseline)
    ):
        if config.override_fixtures:
            restore_conftest(original_conftest_content)
        cleanup_paths(paths_to_cleanup)
        return Err(error="The threshold for test confidence was not met.")

    return Ok(
        (
            function_to_optimize_qualified_name,
            function_to_all_tests,
            original_code_baseline,
            test_functions_to_remove,
            file_path_to_helper_classes,
        )
    )
