from __future__ import annotations

from io import StringIO
from pathlib import Path

from pydantic.dataclasses import dataclass

from codeflash.code_utils.time_utils import humanize_runtime
from codeflash.models.domain import TestResults
from codeflash.models.coverage import BenchmarkDetail
from codeflash.result.critic import throughput_gain


@dataclass(frozen=True, config={"arbitrary_types_allowed": True})
class Explanation:
    raw_explanation_message: str
    winning_behavior_test_results: TestResults
    winning_benchmarking_test_results: TestResults
    original_runtime_ns: int
    best_runtime_ns: int
    function_name: str
    file_path: Path
    benchmark_details: list[BenchmarkDetail] | None = None
    original_async_throughput: int | None = None
    best_async_throughput: int | None = None

    @property
    def perf_improvement_line(self) -> str:
        # speedup property already handles choosing between runtime and throughput
        return f"{self.speedup_pct} improvement ({self.speedup_x} faster)."

    @property
    def speedup(self) -> float:
        runtime_improvement = (self.original_runtime_ns / self.best_runtime_ns) - 1

        # Use throughput improvement if we have async metrics and throughput is better
        if (
            self.original_async_throughput is not None
            and self.best_async_throughput is not None
            and self.original_async_throughput > 0
        ):
            throughput_improvement = throughput_gain(
                original_throughput=self.original_async_throughput,
                optimized_throughput=self.best_async_throughput,
            )

            # Use throughput metrics if throughput improvement is better or runtime got worse
            if throughput_improvement > runtime_improvement or runtime_improvement <= 0:
                return throughput_improvement

        return runtime_improvement

    @property
    def speedup_x(self) -> str:
        return f"{self.speedup:,.2f}x"

    @property
    def speedup_pct(self) -> str:
        return f"{self.speedup * 100:,.0f}%"

    def __str__(self) -> str:
        # TODO: After doing the best optimization, remove the test cases that errored on the new code, because they might be failing because of syntax errors and such.
        # TODO: Sometimes the explanation says something similar to "This is the code that was optimized", remove such parts
        original_runtime_human = humanize_runtime(self.original_runtime_ns)
        best_runtime_human = humanize_runtime(self.best_runtime_ns)

        # Determine if we're showing throughput or runtime improvements
        benchmark_info = ""

        if self.benchmark_details:
            string_buffer = StringIO()
            print("Benchmark Performance Details", file=string_buffer)
            header = f"{'Benchmark Module Path':<40} {'Test Function':<30} {'Original Runtime':<20} {'Expected New Runtime':<25} {'Speedup':<10}"
            print(header, file=string_buffer)
            print("-" * len(header), file=string_buffer)
            for detail in self.benchmark_details:
                print(
                    f"{detail.benchmark_name:<40} {detail.test_function:<30} {detail.original_timing:<20} "
                    f"{detail.expected_new_timing:<25} {detail.speedup_percent:.2f}%",
                    file=string_buffer,
                )
            benchmark_info = string_buffer.getvalue() + "\n"

        if (
            self.original_async_throughput is not None
            and self.best_async_throughput is not None
        ):
            performance_description = (
                f"Throughput improved from {self.original_async_throughput} to {self.best_async_throughput} operations/second "
                f"(runtime: {original_runtime_human} → {best_runtime_human})\n\n"
            )
        else:
            performance_description = f"Runtime went down from {original_runtime_human} to {best_runtime_human} \n\n"

        return (
            f"Optimized {self.function_name} in {self.file_path}\n"
            f"{self.perf_improvement_line}\n"
            + performance_description
            + (benchmark_info if benchmark_info else "")
            + self.raw_explanation_message
            + " \n\n"
            + "The new optimized code was tested for correctness. The results are listed below.\n"
            f"{TestResults.report_to_string(self.winning_behavior_test_results.get_test_pass_fail_report_by_type())}\n"
        )

    def explanation_message(self) -> str:
        return self.raw_explanation_message
