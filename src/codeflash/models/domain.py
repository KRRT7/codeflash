"""Core domain models for codeflash."""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Annotated, cast

import libcst as cst
from jedi.api.classes import Name
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
)
from pydantic.dataclasses import dataclass

from codeflash._constants import VerificationType
from codeflash.cli_cmds.logging_config import DEBUG_MODE, logger
from codeflash.code_utils.diff_utils import diff_length
from codeflash.code_utils.path_utils import module_name_from_file_path
from codeflash.code_utils.validation import validate_python_code
from codeflash.models.api import OptimizedCandidateSource, CoverReturnCode
from codeflash.models.coverage import (
    BenchmarkKey,
    CoverageData,
)
from codeflash.models.test_type import TestType
from codeflash.verification.comparator import comparator


class ExperimentMetadata(BaseModel):
    id: str | None = None
    group: str


class ValidCode(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_code: str
    normalized_code: str


@dataclass(frozen=True, config={"arbitrary_types_allowed": True})
class FunctionSource:
    file_path: Path
    qualified_name: str
    fully_qualified_name: str
    only_function_name: str
    source_code: str
    jedi_definition: Name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FunctionSource):
            return False
        return (
            self.file_path == other.file_path
            and self.qualified_name == other.qualified_name
            and self.fully_qualified_name == other.fully_qualified_name
            and self.only_function_name == other.only_function_name
            and self.source_code == other.source_code
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.file_path,
                self.qualified_name,
                self.fully_qualified_name,
                self.only_function_name,
                self.source_code,
            )
        )


class BestOptimization(BaseModel):
    candidate: OptimizedCandidate
    explanation_v2: str | None = None
    helper_functions: list[FunctionSource]
    code_context: CodeOptimizationContext
    runtime: int
    replay_performance_gain: dict[BenchmarkKey, float] | None = None
    winning_behavior_test_results: TestResults
    winning_benchmarking_test_results: TestResults
    winning_replay_benchmarking_test_results: TestResults | None = None
    line_profiler_test_results: dict
    async_throughput: int | None = None


class CodeString(BaseModel):
    code: Annotated[str, AfterValidator(validate_python_code)]
    file_path: Path | None = None


def get_code_block_splitter(file_path: Path) -> str:
    return f"# file: {file_path.as_posix()}"


markdown_pattern = re.compile(r"```python:([^\n]+)\n(.*?)\n```", re.DOTALL)


class CodeStringsMarkdown(BaseModel):
    code_strings: list[CodeString] = []
    _cache: dict = PrivateAttr(default_factory=dict)

    @property
    def flat(self) -> str:
        if self._cache.get("flat") is not None:
            return self._cache["flat"]
        self._cache["flat"] = "\n".join(
            get_code_block_splitter(block.file_path) + "\n" + block.code
            for block in self.code_strings
        )
        return self._cache["flat"]

    @property
    def markdown(self) -> str:
        return "\n".join(
            f"```python{':' + cs.file_path.as_posix() if cs.file_path else ''}\n{cs.code.strip()}\n```"
            for cs in self.code_strings
        )

    def file_to_path(self) -> dict[str, str]:
        if self._cache.get("file_to_path") is not None:
            return self._cache["file_to_path"]
        self._cache["file_to_path"] = {
            str(cs.file_path): cs.code for cs in self.code_strings
        }
        return self._cache["file_to_path"]

    @staticmethod
    def parse_markdown_code(markdown_code: str) -> CodeStringsMarkdown:
        matches = markdown_pattern.findall(markdown_code)
        code_string_list = []
        try:
            for file_path, code in matches:
                code_string_list.append(
                    CodeString(code=code, file_path=Path(file_path.strip()))
                )
            return CodeStringsMarkdown(code_strings=code_string_list)
        except ValidationError:
            return CodeStringsMarkdown()


class CodeOptimizationContext(BaseModel):
    testgen_context: CodeStringsMarkdown
    read_writable_code: CodeStringsMarkdown
    read_only_context_code: str = ""
    hashing_code_context: str = ""
    hashing_code_context_hash: str = ""
    helper_functions: list[FunctionSource]
    preexisting_objects: set[tuple[str, tuple[FunctionParent, ...]]]


class CodeContextType(str, Enum):
    READ_WRITABLE = "READ_WRITABLE"
    READ_ONLY = "READ_ONLY"
    TESTGEN = "TESTGEN"
    HASHING = "HASHING"


@dataclass(frozen=True)
class OptimizedCandidate:
    source_code: CodeStringsMarkdown
    explanation: str
    optimization_id: str
    source: OptimizedCandidateSource
    parent_id: str | None = None
    model: str | None = None


class OptimizedCandidateResult(BaseModel):
    max_loop_count: int
    best_test_runtime: int
    behavior_test_results: TestResults
    benchmarking_test_results: TestResults
    replay_benchmarking_test_results: dict[BenchmarkKey, TestResults] | None = None
    optimization_candidate_index: int
    total_candidate_timing: int
    async_throughput: int | None = None


class GeneratedTests(BaseModel):
    generated_original_test_source: str
    instrumented_behavior_test_source: str
    instrumented_perf_test_source: str
    behavior_file_path: Path
    perf_file_path: Path


class GeneratedTestsList(BaseModel):
    generated_tests: list[GeneratedTests]


class TestFile(BaseModel):
    instrumented_behavior_file_path: Path
    benchmarking_file_path: Path = None
    original_file_path: Path | None = None
    original_source: str | None = None
    test_type: TestType
    tests_in_file: list[TestsInFile] | None = None


class TestFiles(BaseModel):
    test_files: list[TestFile]

    def get_by_type(self, test_type: TestType) -> TestFiles:
        return TestFiles(
            test_files=[tf for tf in self.test_files if tf.test_type == test_type]
        )

    def add(self, test_file: TestFile) -> None:
        if test_file not in self.test_files:
            self.test_files.append(test_file)
        else:
            msg = "Test file already exists in the list"
            raise ValueError(msg)

    def get_by_original_file_path(self, file_path: Path) -> TestFile | None:
        return next(
            (tf for tf in self.test_files if tf.original_file_path == file_path), None
        )

    def get_test_type_by_instrumented_file_path(
        self, file_path: Path
    ) -> TestType | None:
        return next(
            (
                tf.test_type
                for tf in self.test_files
                if file_path
                in (tf.instrumented_behavior_file_path, tf.benchmarking_file_path)
            ),
            None,
        )

    def get_test_type_by_original_file_path(self, file_path: Path) -> TestType | None:
        return next(
            (
                tf.test_type
                for tf in self.test_files
                if tf.original_file_path == file_path
            ),
            None,
        )

    def __iter__(self) -> Iterator[TestFile]:
        return iter(self.test_files)

    def __len__(self) -> int:
        return len(self.test_files)


class OptimizationSet(BaseModel):
    control: list[OptimizedCandidate]
    experiment: list[OptimizedCandidate] | None


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


@dataclass(frozen=True)
class TestsInFile:
    test_file: Path
    test_class: str | None
    test_function: str
    test_type: TestType


@dataclass(frozen=True)
class FunctionCalledInTest:
    tests_in_file: TestsInFile
    position: CodePosition


@dataclass(frozen=True)
class CodePosition:
    line_no: int
    col_no: int


@dataclass(frozen=True)
class FunctionParent:
    name: str
    type: str


class OriginalCodeBaseline(BaseModel):
    behavior_test_results: TestResults
    benchmarking_test_results: TestResults
    replay_benchmarking_test_results: dict[BenchmarkKey, TestResults] | None = None
    line_profile_results: dict
    runtime: int
    coverage_results: CoverageData | None
    async_throughput: int | None = None


@dataclass(frozen=True)
class InvocationId:
    test_module_path: str
    test_class_name: str | None
    test_function_name: str | None
    function_getting_tested: str
    iteration_id: str | None

    def id(self) -> str:
        class_prefix = f"{self.test_class_name}." if self.test_class_name else ""
        return f"{self.test_module_path}:{class_prefix}{self.test_function_name}:{self.function_getting_tested}:{self.iteration_id}"

    def test_fn_qualified_name(self) -> str:
        return (
            f"{self.test_class_name}.{self.test_function_name}"
            if self.test_class_name
            else str(self.test_function_name)
        )

    def find_func_in_class(
        self, class_node: cst.ClassDef, func_name: str
    ) -> cst.FunctionDef | None:
        for stmt in class_node.body.body:
            if isinstance(stmt, cst.FunctionDef) and stmt.name.value == func_name:
                return stmt
        return None

    def get_src_code(self, test_path: Path) -> str | None:
        if not test_path.exists():
            return None
        try:
            test_src = test_path.read_text(encoding="utf-8")
            module_node = cst.parse_module(test_src)
        except Exception:
            return None
        if self.test_class_name:
            for stmt in module_node.body:
                if (
                    isinstance(stmt, cst.ClassDef)
                    and stmt.name.value == self.test_class_name
                ):
                    func_node = self.find_func_in_class(stmt, self.test_function_name)
                    if func_node:
                        return module_node.code_for_node(func_node).strip()
            return None
        for stmt in module_node.body:
            if (
                isinstance(stmt, cst.FunctionDef)
                and stmt.name.value == self.test_function_name
            ):
                return module_node.code_for_node(stmt).strip()
        return None

    @staticmethod
    def from_str_id(string_id: str, iteration_id: str | None = None) -> InvocationId:
        components = string_id.split(":")
        assert len(components) == 4
        second_components = components[1].split(".")
        if len(second_components) == 1:
            return InvocationId(
                test_module_path=components[0],
                test_class_name=None,
                test_function_name=second_components[0],
                function_getting_tested=components[2],
                iteration_id=iteration_id if iteration_id else components[3],
            )
        return InvocationId(
            test_module_path=components[0],
            test_class_name=second_components[0],
            test_function_name=second_components[1],
            function_getting_tested=components[2],
            iteration_id=iteration_id if iteration_id else components[3],
        )


@dataclass(frozen=True)
class FunctionTestInvocation:
    loop_index: int
    id: InvocationId
    file_name: Path
    did_pass: bool
    runtime: int | None
    test_framework: str
    test_type: TestType
    return_value: object | None
    timed_out: bool | None
    verification_type: str | None = VerificationType.FUNCTION_CALL
    stdout: str | None = None

    @property
    def unique_invocation_loop_id(self) -> str:
        return f"{self.loop_index}:{self.id.id()}"


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
        test_results_by_benchmark = defaultdict(TestResults)
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
        map_gen_test_file_to_no_of_tests = Counter()
        for gen_test_result in self.test_results:
            if (
                gen_test_result.test_type == TestType.GENERATED_REGRESSION
                and gen_test_result.id.test_function_name
                not in test_functions_to_remove
            ):
                map_gen_test_file_to_no_of_tests[gen_test_result.file_name] += 1
        return map_gen_test_file_to_no_of_tests

    def __iter__(self) -> Iterator[FunctionTestInvocation]:
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
