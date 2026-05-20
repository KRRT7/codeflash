from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from tabulate import tabulate  # type: ignore[import-untyped]

from codeflash.code_utils.time_utils import format_perf, format_time
from codeflash.result.critic import performance_gain

if TYPE_CHECKING:
    from codeflash.models.domain import FunctionCalledInTest
    from codeflash.models.invocation_id import InvocationId
    from codeflash.verification.verification_utils import TestConfig


def existing_tests_source_for(
    function_qualified_name_with_modules_from_root: str,
    function_to_tests: dict[str, set[FunctionCalledInTest]],
    test_cfg: TestConfig,
    original_runtimes_all: dict[InvocationId, list[int]],
    optimized_runtimes_all: dict[InvocationId, list[int]],
) -> tuple[str, str, str]:
    test_files = function_to_tests.get(function_qualified_name_with_modules_from_root)
    if not test_files:
        return "", "", ""
    output_existing: str = ""
    output_concolic: str = ""
    output_replay: str = ""
    rows_existing = []
    rows_concolic = []
    rows_replay = []
    headers = ["Test File::Test Function", "Original ⏱️", "Optimized ⏱️", "Speedup"]
    tests_root = test_cfg.tests_root
    original_tests_to_runtimes: dict[Path, dict[str, int]] = {}
    optimized_tests_to_runtimes: dict[Path, dict[str, int]] = {}
    non_generated_tests = set()
    for test_file in test_files:
        non_generated_tests.add(test_file.tests_in_file.test_file)
    all_invocation_ids = original_runtimes_all.keys() | optimized_runtimes_all.keys()
    for invocation_id in all_invocation_ids:
        abs_path = (
            Path(invocation_id.test_module_path.replace(".", os.sep))
            .with_suffix(".py")
            .resolve()
        )
        if abs_path not in non_generated_tests:
            continue
        if abs_path not in original_tests_to_runtimes:
            original_tests_to_runtimes[abs_path] = {}
        if abs_path not in optimized_tests_to_runtimes:
            optimized_tests_to_runtimes[abs_path] = {}
        qualified_name = (
            invocation_id.test_class_name + "." + invocation_id.test_function_name  # type: ignore[operator]
            if invocation_id.test_class_name
            else invocation_id.test_function_name
        )
        if qualified_name not in original_tests_to_runtimes[abs_path]:
            original_tests_to_runtimes[abs_path][qualified_name] = 0  # type: ignore[index]
        if qualified_name not in optimized_tests_to_runtimes[abs_path]:
            optimized_tests_to_runtimes[abs_path][qualified_name] = 0  # type: ignore[index]
        if invocation_id in original_runtimes_all:
            original_tests_to_runtimes[abs_path][qualified_name] += min(  # type: ignore[index]
                original_runtimes_all[invocation_id]
            )
        if invocation_id in optimized_runtimes_all:
            optimized_tests_to_runtimes[abs_path][qualified_name] += min(  # type: ignore[index]
                optimized_runtimes_all[invocation_id]
            )
    all_abs_paths = original_tests_to_runtimes.keys()
    for filename in sorted(all_abs_paths):
        all_qualified_names = original_tests_to_runtimes[filename].keys()
        for qualified_name in sorted(all_qualified_names):
            if (
                original_tests_to_runtimes[filename][qualified_name] != 0
                and optimized_tests_to_runtimes[filename][qualified_name] != 0
            ):
                print_optimized_runtime = format_time(
                    optimized_tests_to_runtimes[filename][qualified_name]
                )
                print_original_runtime = format_time(
                    original_tests_to_runtimes[filename][qualified_name]
                )
                print_filename = (
                    filename.resolve().relative_to(tests_root.resolve()).as_posix()
                )
                greater = (
                    optimized_tests_to_runtimes[filename][qualified_name]
                    > original_tests_to_runtimes[filename][qualified_name]
                )
                perf_gain = format_perf(
                    performance_gain(
                        original_runtime_ns=original_tests_to_runtimes[filename][
                            qualified_name
                        ],
                        optimized_runtime_ns=optimized_tests_to_runtimes[filename][
                            qualified_name
                        ],
                    )
                    * 100
                )
                if greater:
                    if "__replay_test_" in str(print_filename):
                        rows_replay.append(
                            [
                                f"`{print_filename}::{qualified_name}`",
                                f"{print_original_runtime}",
                                f"{print_optimized_runtime}",
                                f"{perf_gain}%⚠️",
                            ]
                        )
                    elif "codeflash_concolic" in str(print_filename):
                        rows_concolic.append(
                            [
                                f"`{print_filename}::{qualified_name}`",
                                f"{print_original_runtime}",
                                f"{print_optimized_runtime}",
                                f"{perf_gain}%⚠️",
                            ]
                        )
                    else:
                        rows_existing.append(
                            [
                                f"`{print_filename}::{qualified_name}`",
                                f"{print_original_runtime}",
                                f"{print_optimized_runtime}",
                                f"{perf_gain}%⚠️",
                            ]
                        )
                elif "__replay_test_" in str(print_filename):
                    rows_replay.append(
                        [
                            f"`{print_filename}::{qualified_name}`",
                            f"{print_original_runtime}",
                            f"{print_optimized_runtime}",
                            f"{perf_gain}%✅",
                        ]
                    )
                elif "codeflash_concolic" in str(print_filename):
                    rows_concolic.append(
                        [
                            f"`{print_filename}::{qualified_name}`",
                            f"{print_original_runtime}",
                            f"{print_optimized_runtime}",
                            f"{perf_gain}%✅",
                        ]
                    )
                else:
                    rows_existing.append(
                        [
                            f"`{print_filename}::{qualified_name}`",
                            f"{print_original_runtime}",
                            f"{print_optimized_runtime}",
                            f"{perf_gain}%✅",
                        ]
                    )
    output_existing += tabulate(
        headers=headers,
        tabular_data=rows_existing,
        tablefmt="pipe",
        colglobalalign=None,
        preserve_whitespace=True,
    )
    output_existing += "\n"
    if len(rows_existing) == 0:
        output_existing = ""
    output_concolic += tabulate(
        headers=headers,
        tabular_data=rows_concolic,
        tablefmt="pipe",
        colglobalalign=None,
        preserve_whitespace=True,
    )
    output_concolic += "\n"
    if len(rows_concolic) == 0:
        output_concolic = ""
    output_replay += tabulate(
        headers=headers,
        tabular_data=rows_replay,
        tablefmt="pipe",
        colglobalalign=None,
        preserve_whitespace=True,
    )
    output_replay += "\n"
    if len(rows_replay) == 0:
        output_replay = ""
    return output_existing, output_replay, output_concolic
