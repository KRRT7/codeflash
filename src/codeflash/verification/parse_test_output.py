from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.cleanup import get_run_tmp_file
from codeflash.discovery.discover_unit_tests import discover_parameters_unittest
from codeflash._constants import VerificationType
from codeflash.models.invocation_id import FunctionTestInvocation
from codeflash.models.test_results import TestResults
from codeflash.verification.coverage_utils import CoverageUtils
from codeflash.verification.parsers.binary_parser import parse_test_return_values_bin
from codeflash.verification.parsers.junit_parser import parse_test_xml
from codeflash.verification.parsers.sqlite_parser import parse_sqlite_test_results

if TYPE_CHECKING:
    import subprocess

    from codeflash.models.coverage import CoverageData
    from codeflash.models.domain import CodeOptimizationContext, TestFiles
    from codeflash.verification.verification_utils import TestConfig


start_pattern = re.compile(r"!\$######([^:]*):([^:]*):([^:]*):([^:]*):([^:]+)######\$!")
end_pattern = re.compile(
    r"!######([^:]*):([^:]*):([^:]*):([^:]*):([^:]+):([^:]+)######!"
)


def calculate_function_throughput_from_test_results(
    test_results: TestResults, function_name: str
) -> int:
    start_matches = start_pattern.findall(test_results.perf_stdout or "")
    end_matches = end_pattern.findall(test_results.perf_stdout or "")

    end_matches_truncated = [end_match[:5] for end_match in end_matches]
    end_matches_set = set(end_matches_truncated)

    function_throughput = 0
    for start_match in start_matches:
        if (
            start_match in end_matches_set
            and len(start_match) > 2
            and start_match[2] == function_name
        ):
            function_throughput += 1
    return function_throughput


def merge_test_results(
    xml_test_results: TestResults, bin_test_results: TestResults, test_framework: str
) -> TestResults:
    merged_test_results = TestResults()

    grouped_xml_results: defaultdict[str, TestResults] = defaultdict(TestResults)
    grouped_bin_results: defaultdict[str, TestResults] = defaultdict(TestResults)

    # This is done to match the right iteration_id which might not be available in the xml
    for result in xml_test_results:
        if test_framework == "pytest":
            if (
                result.id.test_function_name.endswith("]")  # type: ignore[union-attr]
                and "[" in result.id.test_function_name  # type: ignore[operator]
            ):  # parameterized test
                test_function_name = result.id.test_function_name[  # type: ignore[index]
                    : result.id.test_function_name.index("[")  # type: ignore[union-attr]
                ]
            else:
                test_function_name = result.id.test_function_name

        if test_framework == "unittest":
            test_function_name = result.id.test_function_name
            is_parameterized, new_test_function_name, _ = discover_parameters_unittest(
                test_function_name  # type: ignore[arg-type]
            )
            if is_parameterized:  # handle parameterized test
                test_function_name = new_test_function_name

        grouped_xml_results[
            (result.id.test_module_path or "")
            + ":"
            + (result.id.test_class_name or "")
            + ":"
            + (test_function_name or "")
            + ":"
            + str(result.loop_index)
        ].add(result)

    for result in bin_test_results:
        grouped_bin_results[
            (result.id.test_module_path or "")
            + ":"
            + (result.id.test_class_name or "")
            + ":"
            + (result.id.test_function_name or "")
            + ":"
            + str(result.loop_index)
        ].add(result)

    for result_id in grouped_xml_results:
        xml_results = grouped_xml_results[result_id]
        bin_results = grouped_bin_results.get(result_id)
        if not bin_results:
            merged_test_results.merge(xml_results)
            continue

        if len(xml_results) == 1:
            xml_result = xml_results[0]
            # This means that we only have one FunctionTestInvocation for this test xml. Match them to the bin results
            # Either a whole test function fails or passes.
            for result_bin in bin_results:
                merged_test_results.add(
                    FunctionTestInvocation(
                        loop_index=xml_result.loop_index,
                        id=result_bin.id,
                        file_name=xml_result.file_name,
                        runtime=result_bin.runtime,
                        test_framework=xml_result.test_framework,
                        did_pass=xml_result.did_pass,
                        test_type=xml_result.test_type,
                        return_value=result_bin.return_value,
                        timed_out=xml_result.timed_out,
                        verification_type=VerificationType(result_bin.verification_type)
                        if result_bin.verification_type
                        else None,
                        stdout=xml_result.stdout,
                    )
                )
        elif xml_results.test_results[0].id.iteration_id is not None:
            # This means that we have multiple iterations of the same test function
            # We need to match the iteration_id to the bin results
            for xml_result in xml_results.test_results:
                try:
                    bin_result = bin_results.get_by_unique_invocation_loop_id(
                        xml_result.unique_invocation_loop_id
                    )
                except AttributeError:
                    bin_result = None
                if bin_result is None:
                    merged_test_results.add(xml_result)
                    continue
                merged_test_results.add(
                    FunctionTestInvocation(
                        loop_index=xml_result.loop_index,
                        id=xml_result.id,
                        file_name=xml_result.file_name,
                        runtime=bin_result.runtime,
                        test_framework=xml_result.test_framework,
                        did_pass=bin_result.did_pass,
                        test_type=xml_result.test_type,
                        return_value=bin_result.return_value,
                        timed_out=xml_result.timed_out
                        if bin_result.runtime is None
                        else False,  # If runtime was measured in the bin file, then the testcase did not time out
                        verification_type=VerificationType(bin_result.verification_type)
                        if bin_result.verification_type
                        else None,
                        stdout=xml_result.stdout,
                    )
                )
        else:
            # Should happen only if the xml did not have any test invocation id info
            for i, bin_result in enumerate(bin_results.test_results):
                try:
                    xml_result = xml_results.test_results[i]
                except IndexError:
                    xml_result = None
                if xml_result is None:
                    merged_test_results.add(bin_result)
                    continue
                merged_test_results.add(
                    FunctionTestInvocation(
                        loop_index=bin_result.loop_index,
                        id=bin_result.id,
                        file_name=bin_result.file_name,
                        runtime=bin_result.runtime,
                        test_framework=bin_result.test_framework,
                        did_pass=bin_result.did_pass,
                        test_type=bin_result.test_type,
                        return_value=bin_result.return_value,
                        timed_out=xml_result.timed_out,  # only the xml gets the timed_out flag
                        verification_type=VerificationType(bin_result.verification_type)
                        if bin_result.verification_type
                        else None,
                        stdout=xml_result.stdout,
                    )
                )

    return merged_test_results


FAILURES_HEADER_RE = re.compile(r"=+ FAILURES =+")
TEST_HEADER_RE = re.compile(r"_{3,}\s*(.*?)\s*_{3,}$")


def parse_test_failures_from_stdout(stdout: str) -> dict[str, str]:
    """Extract individual pytest test failures from stdout grouped by test case qualified name, and add them to the test results."""
    lines = stdout.splitlines()
    start = end = None

    for i, line in enumerate(lines):
        if FAILURES_HEADER_RE.search(line.strip()):
            start = i
            break

    if start is None:
        return {}

    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if "short test summary info" in stripped:
            end = j
            break
        # any new === section === block
        if stripped.startswith("=") and stripped.count("=") > 3:
            end = j
            break

    # If no clear "end", just grap the rest of the string
    if end is None:
        end = len(lines)

    failure_block = lines[start:end]

    failures: dict[str, str] = {}
    current_name = None
    current_lines: list[str] = []

    for line in failure_block:
        m = TEST_HEADER_RE.match(line.strip())
        if m:
            if current_name is not None:
                failures[current_name] = "".join(current_lines)

            current_name = m.group(1)
            current_lines = []
        elif current_name:
            current_lines.append(line + "\n")

    if current_name:
        failures[current_name] = "".join(current_lines)

    return failures


def parse_test_results(
    test_xml_path: Path,
    test_files: TestFiles,
    test_config: TestConfig,
    optimization_iteration: int,
    function_name: str | None,
    source_file: Path | None,
    coverage_database_file: Path | None,
    coverage_config_file: Path | None,
    code_context: CodeOptimizationContext | None = None,
    run_result: subprocess.CompletedProcess | None = None,
) -> tuple[TestResults, CoverageData | None]:
    test_results_xml = parse_test_xml(
        test_xml_path,
        test_files=test_files,
        test_config=test_config,
        run_result=run_result,
    )
    try:
        bin_results_file = get_run_tmp_file(
            Path(f"test_return_values_{optimization_iteration}.bin")
        )
        test_results_bin_file = (
            parse_test_return_values_bin(
                bin_results_file, test_files=test_files, test_config=test_config
            )
            if bin_results_file.exists()
            else TestResults()
        )
    except AttributeError as e:
        logger.exception(e)
        test_results_bin_file = TestResults()
        get_run_tmp_file(
            Path(f"test_return_values_{optimization_iteration}.bin")
        ).unlink(missing_ok=True)

    try:
        sql_results_file = get_run_tmp_file(
            Path(f"test_return_values_{optimization_iteration}.sqlite")
        )
        if sql_results_file.exists():
            test_results_sqlite_file = parse_sqlite_test_results(
                sqlite_file_path=sql_results_file,
                test_files=test_files,
                test_config=test_config,
            )
            test_results_bin_file.merge(test_results_sqlite_file)
    except AttributeError as e:
        logger.exception(e)

    get_run_tmp_file(Path(f"test_return_values_{optimization_iteration}.bin")).unlink(
        missing_ok=True
    )

    get_run_tmp_file(Path("pytest_results.xml")).unlink(missing_ok=True)
    get_run_tmp_file(Path("unittest_results.xml")).unlink(missing_ok=True)
    get_run_tmp_file(
        Path(f"test_return_values_{optimization_iteration}.sqlite")
    ).unlink(missing_ok=True)
    results = merge_test_results(
        test_results_xml, test_results_bin_file, test_config.test_framework
    )

    all_args = False
    if coverage_database_file and source_file and code_context and function_name:
        all_args = True
        coverage = CoverageUtils.load_from_sqlite_database(
            database_path=coverage_database_file,
            config_path=coverage_config_file,  # type: ignore[arg-type]
            source_code_path=source_file,
            code_context=code_context,
            function_name=function_name,
        )
        coverage.log_coverage()
    try:
        failures = parse_test_failures_from_stdout(run_result.stdout)  # type: ignore[union-attr]
        results.test_failures = failures
    except Exception as e:
        logger.exception(e)

    return results, coverage if all_args else None
