from __future__ import annotations

import enum
import re
from collections.abc import Collection
from pathlib import Path
from re import Pattern
from typing import Any

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass


class BenchmarkKey:
    """Frozen-like key type for benchmark lookups."""

    def __init__(self, module_path: str, function_name: str) -> None:
        self.module_path = module_path
        self.function_name = function_name

    def __str__(self) -> str:
        return f"{self.module_path}::{self.function_name}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BenchmarkKey):
            return NotImplemented
        return (
            self.module_path == other.module_path
            and self.function_name == other.function_name
        )

    def __hash__(self) -> int:
        return hash((self.module_path, self.function_name))


@dataclass
class BenchmarkDetail:
    benchmark_name: str
    test_function: str
    original_timing: str
    expected_new_timing: str
    speedup_percent: float

    def to_string(self) -> str:
        return (
            f"Original timing for {self.benchmark_name}::{self.test_function}: {self.original_timing}\n"
            f"Expected new timing for {self.benchmark_name}::{self.test_function}: {self.expected_new_timing}\n"
            f"Benchmark speedup for {self.benchmark_name}::{self.test_function}: {self.speedup_percent:.2f}%\n"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_name": self.benchmark_name,
            "test_function": self.test_function,
            "original_timing": self.original_timing,
            "expected_new_timing": self.expected_new_timing,
            "speedup_percent": self.speedup_percent,
        }


@dataclass
class ProcessedBenchmarkInfo:
    benchmark_details: list[BenchmarkDetail]

    def to_string(self) -> str:
        if not self.benchmark_details:
            return ""
        result = "Benchmark Performance Details:\n"
        for detail in self.benchmark_details:
            result += detail.to_string() + "\n"
        return result

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {"benchmark_details": [d.to_dict() for d in self.benchmark_details]}


class TestingMode(enum.Enum):
    BEHAVIOR = "behavior"
    PERFORMANCE = "performance"
    LINE_PROFILE = "line_profile"


class CoverageStatus(enum.Enum):
    NOT_FOUND = "Coverage Data Not Found"
    PARSED_SUCCESSFULLY = "Parsed Successfully"


@dataclass
class FunctionCoverage:
    name: str
    coverage: float
    executed_lines: list[int]
    unexecuted_lines: list[int]
    executed_branches: list[list[int]]
    unexecuted_branches: list[list[int]]


@dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class CoverageData:
    file_path: Path
    coverage: float
    function_name: str
    functions_being_tested: list[str]
    graph: dict[str, dict[str, Collection[object]]]
    code_context: object
    main_func_coverage: FunctionCoverage
    dependent_func_coverage: FunctionCoverage | None
    status: CoverageStatus
    blank_re: Pattern[str] = re.compile(r"\s*(#|$)")
    else_re: Pattern[str] = re.compile(r"\s*else\s*:\s*(#|$)")

    def build_message(self) -> str:
        if self.status == CoverageStatus.NOT_FOUND:
            return f"No coverage data found for {self.function_name}"
        return f"{self.coverage:.1f}%"

    def log_coverage(self) -> None:
        print("Test Coverage Results")
        print(f"  Main Function: {self.main_func_coverage.name}: {self.coverage:.2f}%")
        if self.dependent_func_coverage:
            print(
                f"  Dependent Function: {self.dependent_func_coverage.name}: {self.dependent_func_coverage.coverage:.2f}%"
            )
        print(f"  Total Coverage: {self.coverage:.2f}%")
        print("─" * 80)
        if not self.coverage:
            print(self.graph)

    @classmethod
    def create_empty(
        cls, file_path: Path, function_name: str, code_context: object
    ) -> CoverageData:
        return cls(
            file_path=file_path,
            coverage=0.0,
            function_name=function_name,
            functions_being_tested=[function_name],
            graph={
                function_name: {
                    "executed_lines": set(),
                    "unexecuted_lines": set(),
                    "executed_branches": [],
                    "unexecuted_branches": [],
                }
            },
            code_context=code_context,
            main_func_coverage=FunctionCoverage(
                name=function_name,
                coverage=0.0,
                executed_lines=[],
                unexecuted_lines=[],
                executed_branches=[],
                unexecuted_branches=[],
            ),
            dependent_func_coverage=None,
            status=CoverageStatus.NOT_FOUND,
        )
