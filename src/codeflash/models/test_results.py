from __future__ import annotations

import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel

from codeflash.cli_cmds.logging_config import DEBUG_MODE, logger
from codeflash.code_utils.path_utils import module_name_from_file_path
from codeflash.models.coverage import BenchmarkKey
from codeflash.models.invocation_id import FunctionTestInvocation, InvocationId
from codeflash.models.test_type import TestType
from codeflash.verification.comparator import comparator

if TYPE_CHECKING:
    from codeflash.models.invocation_id import InvocationId


class TestResults(BaseModel):
    test_results: list[FunctionTestInvocation] = []
    test_result_idx: dict[str, int] = {}
    perf_stdout: str | None = None
    test_failures: dict[str, str] | None = None

    def add(self, function_test_invocation: FunctionTestInvocation) -> None:
        unique_id = function_test_invocation.unique_invocation_loop_id
        if unique_id in self.test_result_idx:
            if DEBUG_MODE:
                logger.warning(
                    f"Test result with id {unique_id} already exists. SKIPPING"
                )
            return
        self.test_result_idx[unique_id] = len(self.test_results)
        self.test_results.append(function_test_invocation)

    def merge(self, other: TestResults) -> None:
        original_len = len(self.test_results)
        self.test_results.extend(other.test_results)
        for k, v in other.test_result_idx.items():
            if k in self.test_result_idx:
                msg = f"Test result with id {k} already exists."
                raise ValueError(msg)
            self.test_result_idx[k] = v + original_len

    def group_by_benchmarks(
        self,
        benchmark_keys: list[BenchmarkKey],
        benchmark_replay_test_dir: Path,
        project_root: Path,
    ) -> dict[BenchmarkKey, TestResults]:
        test_results_by_benchmark: dict[BenchmarkKey, TestResults] = defaultdict(
            TestResults
        )
        benchmark_module_path = {}
        for benchmark_key in benchmark_keys:
            benchmark_module_path[benchmark_key] = module_name_from_file_path(
                benchmark_replay_test_dir.resolve()
                / f"test_{benchmark_key.module_path.replace('.', '_')}__replay_test_",
                project_root,
                traverse_up=True,
            )
        for test_result in self.test_results:
            if test_result.test_type == TestType.REPLAY_TEST:
                for benchmark_key, module_path in benchmark_module_path.items():
                    if test_result.id.test_module_path.startswith(module_path):
                        test_results_by_benchmark[benchmark_key].add(test_result)
        return test_results_by_benchmark

    def get_by_unique_invocation_loop_id(
        self, unique_invocation_loop_id: str
    ) -> FunctionTestInvocation | None:
        try:
            return self.test_results[self.test_result_idx[unique_invocation_loop_id]]
        except (IndexError, KeyError):
            return None

    def get_all_ids(self) -> set[InvocationId]:
        return {test_result.id for test_result in self.test_results}

    def get_all_unique_invocation_loop_ids(self) -> set[str]:
        return {
            test_result.unique_invocation_loop_id for test_result in self.test_results
        }

    def number_of_loops(self) -> int:
        if not self.test_results:
            return 0
        return max(test_result.loop_index for test_result in self.test_results)

    def get_test_pass_fail_report_by_type(self) -> dict[TestType, dict[str, int]]:
        report = {}
        for test_type in TestType:
            report[test_type] = {"passed": 0, "failed": 0}
        for test_result in self.test_results:
            if test_result.loop_index == 1:
                if test_result.did_pass:
                    report[test_result.test_type]["passed"] += 1
                else:
                    report[test_result.test_type]["failed"] += 1
        return report

    @staticmethod
    def report_to_string(report: dict[TestType, dict[str, int]]) -> str:
        return " ".join(
            f"{test_type.to_name()}- (Passed: {report[test_type]['passed']}, Failed: {report[test_type]['failed']})"
            for test_type in TestType
        )

    @staticmethod
    def report_to_tree(report: dict[TestType, dict[str, int]], title: str) -> str:
        lines = [title]
        for test_type in TestType:
            if test_type is TestType.INIT_STATE_TEST:
                continue
            lines.append(
                f"  {test_type.to_name()} - Passed: {report[test_type]['passed']}, Failed: {report[test_type]['failed']}"
            )
        return "\n".join(lines)

    def usable_runtime_data_by_test_case(self) -> dict[InvocationId, list[int]]:
        by_id: dict[InvocationId, list[int]] = {}
        for result in self.test_results:
            if result.did_pass and result.runtime:
                by_id.setdefault(result.id, []).append(result.runtime)
        return by_id

    def total_passed_runtime(self) -> int:
        return sum(
            min(data) for _, data in self.usable_runtime_data_by_test_case().items()
        )

    def file_to_no_of_tests(self, test_functions_to_remove: list[str]) -> Counter[Path]:
        map_gen_test_file_to_no_of_tests: Counter[Path] = Counter()
        for gen_test_result in self.test_results:
            if (
                gen_test_result.test_type == TestType.GENERATED_REGRESSION
                and gen_test_result.id.test_function_name
                not in test_functions_to_remove
            ):
                map_gen_test_file_to_no_of_tests[gen_test_result.file_name] += 1
        return map_gen_test_file_to_no_of_tests

    def __iter__(self) -> Iterator[FunctionTestInvocation]:  # type: ignore[override]
        return iter(self.test_results)

    def __len__(self) -> int:
        return len(self.test_results)

    def __getitem__(self, index: int) -> FunctionTestInvocation:
        return self.test_results[index]

    def __setitem__(self, index: int, value: FunctionTestInvocation) -> None:
        self.test_results[index] = value

    def __contains__(self, value: FunctionTestInvocation) -> bool:
        return value in self.test_results

    def __bool__(self) -> bool:
        return bool(self.test_results)

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return False
        if len(self) != len(other):
            return False
        original_recursion_limit = sys.getrecursionlimit()
        cast("TestResults", other)
        for test_result in self:
            other_test_result = other.get_by_unique_invocation_loop_id(
                test_result.unique_invocation_loop_id
            )
            if other_test_result is None:
                return False
            if original_recursion_limit < 5000:
                sys.setrecursionlimit(5000)
            if (
                test_result.file_name != other_test_result.file_name
                or test_result.did_pass != other_test_result.did_pass
                or test_result.runtime != other_test_result.runtime
                or test_result.test_framework != other_test_result.test_framework
                or test_result.test_type != other_test_result.test_type
                or not comparator(
                    test_result.return_value, other_test_result.return_value
                )
            ):
                sys.setrecursionlimit(original_recursion_limit)
                return False
        sys.setrecursionlimit(original_recursion_limit)
        return True
