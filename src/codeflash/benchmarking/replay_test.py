from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.formatter import sort_imports
from codeflash.benchmarking.replay_test_codegen import create_trace_replay_test_code
from codeflash.discovery.functions_to_optimize import (
    inspect_top_level_functions_or_methods,
)
from codeflash.verification.verification_utils import get_test_file_path

if TYPE_CHECKING:
    from collections.abc import Generator


def get_next_arg_and_return(
    trace_file: str,
    benchmark_function_name: str,
    function_name: str,
    file_path: str,
    class_name: str | None = None,
    num_to_get: int = 256,
) -> Generator[Any]:
    db = sqlite3.connect(trace_file)
    cur = db.cursor()
    limit = num_to_get

    normalized_file_path = Path(file_path).as_posix()

    if class_name is not None:
        cursor = cur.execute(
            "SELECT * FROM benchmark_function_timings WHERE benchmark_function_name = ? AND function_name = ? AND file_path = ? AND class_name = ? LIMIT ?",
            (
                benchmark_function_name,
                function_name,
                normalized_file_path,
                class_name,
                limit,
            ),
        )
    else:
        cursor = cur.execute(
            "SELECT * FROM benchmark_function_timings WHERE benchmark_function_name = ? AND function_name = ? AND file_path = ? AND class_name = '' LIMIT ?",
            (benchmark_function_name, function_name, normalized_file_path, limit),
        )

    try:
        while (val := cursor.fetchone()) is not None:
            yield val[9], val[10]  # pickled_args, pickled_kwargs
    finally:
        db.close()


def generate_replay_test(
    trace_file_path: Path, output_dir: Path, max_run_count: int = 100
) -> int:
    """Generate multiple replay tests from the traced function calls, grouped by benchmark.

    Args:
    ----
        trace_file_path: Path to the SQLite database file
        output_dir: Directory to write the generated tests (if None, only returns the code)
        max_run_count: Maximum number of runs to include per function

    Returns:
    -------
        The number of replay tests generated

    """
    count = 0
    try:
        # Connect to the database
        conn = sqlite3.connect(trace_file_path.as_posix())
        cursor = conn.cursor()

        # Get distinct benchmark file paths
        cursor.execute(
            "SELECT DISTINCT benchmark_module_path FROM benchmark_function_timings"
        )
        benchmark_files = cursor.fetchall()

        # Generate a test for each benchmark file
        for benchmark_file in benchmark_files:
            benchmark_module_path = benchmark_file[0]
            # Get all benchmarks and functions associated with this file path
            cursor.execute(
                "SELECT DISTINCT benchmark_function_name, function_name, class_name, module_name, file_path, benchmark_line_number FROM benchmark_function_timings "
                "WHERE benchmark_module_path = ?",
                (benchmark_module_path,),
            )

            functions_data = []
            for row in cursor.fetchall():
                (
                    benchmark_function_name,
                    function_name,
                    class_name,
                    module_name,
                    file_path,
                    benchmark_line_number,
                ) = row
                # Add this function to our list
                functions_data.append(
                    {
                        "function_name": function_name,
                        "class_name": class_name,
                        "file_path": file_path,
                        "module_name": module_name,
                        "benchmark_function_name": benchmark_function_name,
                        "benchmark_module_path": benchmark_module_path,
                        "benchmark_line_number": benchmark_line_number,
                        "function_properties": inspect_top_level_functions_or_methods(
                            file_name=Path(file_path),
                            function_or_method_name=function_name,
                            class_name=class_name,
                        ),
                    }
                )

            if not functions_data:
                logger.info(
                    f"No benchmark test functions found in {benchmark_module_path}"
                )
                continue
            # Generate the test code for this benchmark
            test_code = create_trace_replay_test_code(
                trace_file=trace_file_path.as_posix(),
                functions_data=functions_data,
                max_run_count=max_run_count,
            )
            test_code = sort_imports(code=test_code)
            output_file = get_test_file_path(
                test_dir=Path(output_dir),
                function_name=benchmark_module_path,
                test_type="replay",
            )
            # Write test code to file, parents = true
            output_dir.mkdir(parents=True, exist_ok=True)
            output_file.write_text(test_code, "utf-8")
            count += 1

        conn.close()
    except Exception as e:
        logger.info(f"Error generating replay tests: {e}")

    return count
