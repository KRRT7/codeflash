"""Core domain models for codeflash."""

from __future__ import annotations

import re
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import Annotated

from jedi.api.classes import Name
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    PrivateAttr,
    ValidationError,
)
from pydantic.dataclasses import dataclass

from codeflash.code_utils.validation import validate_python_code
from codeflash.models.api import OptimizedCandidateSource
from codeflash.models.coverage import BenchmarkKey, CoverageData
from codeflash.models.test_results import TestResults
from codeflash.models.test_type import TestType


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
    model_config = ConfigDict(arbitrary_types_allowed=True)
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
            if block.file_path is not None
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
    model_config = ConfigDict(arbitrary_types_allowed=True)
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
    benchmarking_file_path: Path | None = None
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

    def __iter__(self) -> Iterator[TestFile]:  # type: ignore[override]
        return iter(self.test_files)

    def __len__(self) -> int:
        return len(self.test_files)


class OptimizationSet(BaseModel):
    control: list[OptimizedCandidate]
    experiment: list[OptimizedCandidate] | None


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
    model_config = ConfigDict(arbitrary_types_allowed=True)
    behavior_test_results: TestResults
    benchmarking_test_results: TestResults
    replay_benchmarking_test_results: dict[BenchmarkKey, TestResults] | None = None
    line_profile_results: dict
    runtime: int
    coverage_results: CoverageData | None
    async_throughput: int | None = None
