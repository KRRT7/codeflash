from __future__ import annotations
from codeflash.code_utils.config_utils import ImportErrorPattern, custom_addopts

# ruff: noqa: SLF001
from codeflash.code_utils.cleanup import get_run_tmp_file
from codeflash.code_utils.path_utils import module_name_from_file_path

import enum
import os
import pickle
import re
import subprocess
import unittest
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Callable, final

if TYPE_CHECKING:
    from codeflash.discovery.functions_to_optimize import FunctionToOptimize
from pydantic.dataclasses import dataclass

from codeflash.cli_cmds.logging_config import logger, rule
from codeflash.code_utils.compat import SAFE_SYS_EXECUTABLE
from codeflash.code_utils.shell_utils import get_cross_platform_subprocess_run_args
from codeflash.discovery.import_analyzer import filter_test_files_by_imports
from codeflash.discovery.tests_cache import TestsCache
from codeflash.models.domain import (
    CodePosition,
    FunctionCalledInTest,
    TestsInFile,
    TestType,
)

if TYPE_CHECKING:
    from codeflash.verification.verification_utils import TestConfig


@final
class PytestExitCode(enum.IntEnum):  # don't need to import entire pytest just for this
    OK = 0
    TESTS_FAILED = 1
    INTERRUPTED = 2
    INTERNAL_ERROR = 3
    USAGE_ERROR = 4
    NO_TESTS_COLLECTED = 5


@dataclass(frozen=True)
class TestFunction:
    function_name: str
    test_class: str | None
    parameters: str | None
    test_type: TestType


ERROR_PATTERN = re.compile(r"={3,}\s*ERRORS\s*={3,}\n([\s\S]*?)(?:={3,}|$)")
PYTEST_PARAMETERIZED_TEST_NAME_REGEX = re.compile(r"[\[\]]")
UNITTEST_PARAMETERIZED_TEST_NAME_REGEX = re.compile(r"^test_\w+_\d+(?:_\w+)*")
UNITTEST_STRIP_NUMBERED_SUFFIX_REGEX = re.compile(r"_\d+(?:_\w+)*$")
FUNCTION_NAME_REGEX = re.compile(r"([^.]+)\.([a-zA-Z0-9_]+)$")


def discover_unit_tests(
    cfg: TestConfig,
    discover_only_these_tests: list[Path] | None = None,
    file_to_funcs_to_optimize: dict[Path, list[FunctionToOptimize]] | None = None,
) -> tuple[dict[str, set[FunctionCalledInTest]], int, int]:
    framework_strategies: dict[str, Callable] = {
        "pytest": discover_tests_pytest,
        "unittest": discover_tests_unittest,
    }
    strategy = framework_strategies.get(cfg.test_framework, None)
    if not strategy:
        error_message = f"Unsupported test framework: {cfg.test_framework}"
        raise ValueError(error_message)

    # Extract all functions to optimize for import filtering
    functions_to_optimize = None
    if file_to_funcs_to_optimize:
        functions_to_optimize = [
            func
            for funcs_list in file_to_funcs_to_optimize.values()
            for func in funcs_list
        ]
    function_to_tests, num_discovered_tests, num_discovered_replay_tests = strategy(
        cfg, discover_only_these_tests, functions_to_optimize
    )
    return function_to_tests, num_discovered_tests, num_discovered_replay_tests


def discover_tests_pytest(
    cfg: TestConfig,
    discover_only_these_tests: list[Path] | None = None,
    functions_to_optimize: list[FunctionToOptimize] | None = None,
) -> tuple[dict[str, set[FunctionCalledInTest]], int, int]:
    tests_root = cfg.tests_root
    project_root = cfg.project_root_path

    tmp_pickle_path = get_run_tmp_file("collected_tests.pkl")
    with custom_addopts():
        run_kwargs = get_cross_platform_subprocess_run_args(
            cwd=project_root, check=False, text=True, capture_output=True
        )
        result = subprocess.run(  # noqa: PLW1510
            [
                SAFE_SYS_EXECUTABLE,
                Path(__file__).parent / "pytest_new_process_discovery.py",
                str(project_root),
                str(tests_root),
                str(tmp_pickle_path),
            ],
            **run_kwargs,
        )
    try:
        with tmp_pickle_path.open(mode="rb") as f:
            exitcode, tests, pytest_rootdir = pickle.load(f)
    except Exception as e:
        tests, pytest_rootdir = [], None
        logger.exception(f"Failed to discover tests: {e}")
        exitcode = -1
    finally:
        tmp_pickle_path.unlink(missing_ok=True)
    if exitcode != 0:
        if exitcode == 2 and "ERROR collecting" in result.stdout:
            # Pattern matches "===== ERRORS =====" (any number of =) and captures everything after
            match = ERROR_PATTERN.search(result.stdout)
            error_section = match.group(1) if match else result.stdout

            logger.warning(
                f"Failed to collect tests. Pytest Exit code: {exitcode}={PytestExitCode(exitcode).name}\n {error_section}"
            )
            if "ModuleNotFoundError" in result.stdout:
                match = ImportErrorPattern.search(result.stdout)
                if match:
                    error_message = match.group()
                    print(f"⚠️  {error_message}")

        elif 0 <= exitcode <= 5:
            logger.warning(
                f"Failed to collect tests. Pytest Exit code: {exitcode}={PytestExitCode(exitcode).name}"
            )
        else:
            logger.warning(f"Failed to collect tests. Pytest Exit code: {exitcode}")
        rule()
    else:
        logger.debug(f"Pytest collection exit code: {exitcode}")
    if pytest_rootdir is not None:
        cfg.tests_project_rootdir = Path(pytest_rootdir)
    file_to_test_map: dict[Path, list[FunctionCalledInTest]] = defaultdict(list)
    for test in tests:
        if "__replay_test" in test["test_file"]:
            test_type = TestType.REPLAY_TEST
        elif "test_concolic_coverage" in test["test_file"]:
            test_type = TestType.CONCOLIC_COVERAGE_TEST
        else:
            test_type = TestType.EXISTING_UNIT_TEST

        test_obj = TestsInFile(
            test_file=Path(test["test_file"]),
            test_class=test["test_class"],
            test_function=test["test_function"],
            test_type=test_type,
        )
        if (
            discover_only_these_tests
            and test_obj.test_file not in discover_only_these_tests
        ):
            continue
        file_to_test_map[test_obj.test_file].append(test_obj)  # type: ignore[arg-type]
    # Within these test files, find the project functions they are referring to and return their names/locations
    return process_test_files(file_to_test_map, cfg, functions_to_optimize)  # type: ignore[arg-type]


def discover_tests_unittest(
    cfg: TestConfig,
    discover_only_these_tests: list[Path] | None = None,
    functions_to_optimize: list[FunctionToOptimize] | None = None,
) -> tuple[dict[str, set[FunctionCalledInTest]], int, int]:
    tests_root: Path = cfg.tests_root
    loader: unittest.TestLoader = unittest.TestLoader()
    tests: unittest.TestSuite = loader.discover(str(tests_root))
    file_to_test_map: defaultdict[Path, list[TestsInFile]] = defaultdict(list)

    def get_test_details(_test: unittest.TestCase) -> TestsInFile | None:
        _test_function, _test_module, _test_suite_name = (
            _test._testMethodName,
            _test.__class__.__module__,
            _test.__class__.__qualname__,
        )

        _test_module_path = Path(_test_module.replace(".", os.sep)).with_suffix(".py")
        _test_module_path = tests_root / _test_module_path
        if not _test_module_path.exists() or (
            discover_only_these_tests
            and _test_module_path not in discover_only_these_tests
        ):
            return None
        if "__replay_test" in str(_test_module_path):
            test_type = TestType.REPLAY_TEST
        elif "test_concolic_coverage" in str(_test_module_path):
            test_type = TestType.CONCOLIC_COVERAGE_TEST
        else:
            test_type = TestType.EXISTING_UNIT_TEST
        return TestsInFile(
            test_file=_test_module_path,
            test_function=_test_function,
            test_type=test_type,
            test_class=_test_suite_name,
        )

    for _test_suite in tests._tests:
        for test_suite_2 in _test_suite._tests:  # type: ignore[attr-defined]
            if not hasattr(test_suite_2, "_tests"):
                logger.warning(f"Didn't find tests for {test_suite_2}")
                continue

            for test in test_suite_2._tests:
                # some test suites are nested, so we need to go deeper
                if not hasattr(test, "_testMethodName") and hasattr(test, "_tests"):
                    for test_2 in test._tests:
                        if not hasattr(test_2, "_testMethodName"):
                            logger.warning(
                                f"Didn't find tests for {test_2}"
                            )  # it goes deeper?
                            continue
                        details = get_test_details(test_2)
                        if details is not None:
                            file_to_test_map[details.test_file].append(details)
                else:
                    details = get_test_details(test)
                    if details is not None:
                        file_to_test_map[details.test_file].append(details)
    return process_test_files(file_to_test_map, cfg, functions_to_optimize)


def discover_parameters_unittest(function_name: str) -> tuple[bool, str, str | None]:
    function_parts = function_name.split("_")
    if len(function_parts) > 1 and function_parts[-1].isdigit():
        return True, "_".join(function_parts[:-1]), function_parts[-1]

    return False, function_name, None


def process_test_files(
    file_to_test_map: dict[Path, list[TestsInFile]],
    cfg: TestConfig,
    functions_to_optimize: list[FunctionToOptimize] | None = None,
) -> tuple[dict[str, set[FunctionCalledInTest]], int, int]:
    import jedi

    project_root_path = cfg.project_root_path
    test_framework = cfg.test_framework

    if functions_to_optimize:
        target_function_names = {func.qualified_name for func in functions_to_optimize}
        file_to_test_map = filter_test_files_by_imports(
            file_to_test_map, target_function_names
        )

    function_to_test_map = defaultdict(set)
    num_discovered_tests = 0
    num_discovered_replay_tests = 0

    # Set up sys_path for Jedi to resolve imports correctly
    import sys

    jedi_sys_path = list(sys.path)
    # Add project root and its parent to sys_path so modules can be imported
    if str(project_root_path) not in jedi_sys_path:
        jedi_sys_path.insert(0, str(project_root_path))
    parent_path = project_root_path.parent
    if str(parent_path) not in jedi_sys_path:
        jedi_sys_path.insert(0, str(parent_path))

    jedi_project = jedi.Project(path=project_root_path, sys_path=jedi_sys_path)

    tests_cache = TestsCache(project_root_path)
    logger.info("Discovering tests and processing unit tests")
    rule()
    for test_file, functions in file_to_test_map.items():
        file_hash = TestsCache.compute_file_hash(test_file)

        cached_function_to_test_map = tests_cache.get_function_to_test_map_for_file(
            str(test_file), file_hash
        )

        if cfg.use_cache and cached_function_to_test_map:
            for qualified_name, test_set in cached_function_to_test_map.items():
                function_to_test_map[qualified_name].update(test_set)

                for function_called_in_test in test_set:
                    if (
                        function_called_in_test.tests_in_file.test_type
                        == TestType.REPLAY_TEST
                    ):
                        num_discovered_replay_tests += 1
                    num_discovered_tests += 1

            continue
        try:
            script = jedi.Script(path=test_file, project=jedi_project)
            test_functions = set()

            all_names = script.get_names(all_scopes=True, references=True)
            all_defs = script.get_names(all_scopes=True, definitions=True)
            all_names_top = script.get_names(all_scopes=True)

            top_level_functions = {
                name.name: name for name in all_names_top if name.type == "function"
            }
            top_level_classes = {
                name.name: name for name in all_names_top if name.type == "class"
            }

        except Exception as e:
            logger.debug(f"Failed to get jedi script for {test_file}: {e}")
            continue

        if test_framework == "pytest":
            for function in functions:
                if "[" in function.test_function:
                    function_name = PYTEST_PARAMETERIZED_TEST_NAME_REGEX.split(
                        function.test_function
                    )[0]
                    parameters = PYTEST_PARAMETERIZED_TEST_NAME_REGEX.split(
                        function.test_function
                    )[1]
                    if function_name in top_level_functions:
                        test_functions.add(
                            TestFunction(
                                function_name,
                                function.test_class,
                                parameters,
                                function.test_type,
                            )
                        )
                elif function.test_function in top_level_functions:
                    test_functions.add(
                        TestFunction(
                            function.test_function,
                            function.test_class,
                            None,
                            function.test_type,
                        )
                    )
                elif UNITTEST_PARAMETERIZED_TEST_NAME_REGEX.match(
                    function.test_function
                ):
                    base_name = UNITTEST_STRIP_NUMBERED_SUFFIX_REGEX.sub(
                        "", function.test_function
                    )
                    if base_name in top_level_functions:
                        test_functions.add(
                            TestFunction(
                                function_name=base_name,
                                test_class=function.test_class,
                                parameters=function.test_function,
                                test_type=function.test_type,
                            )
                        )

        elif test_framework == "unittest":
            functions_to_search = [elem.test_function for elem in functions]
            test_suites = {elem.test_class for elem in functions}

            matching_names = test_suites & top_level_classes.keys()
            for matched_name in matching_names:
                for def_name in all_defs:
                    if (
                        def_name.type == "function"
                        and def_name.full_name is not None
                        and f".{matched_name}." in def_name.full_name
                    ):
                        for function in functions_to_search:  # type: ignore[assignment]
                            (is_parameterized, new_function, parameters) = (
                                discover_parameters_unittest(function)  # type: ignore[arg-type]
                            )

                            if is_parameterized and new_function == def_name.name:
                                test_functions.add(
                                    TestFunction(
                                        function_name=def_name.name,
                                        test_class=matched_name,
                                        parameters=parameters,
                                        test_type=functions[0].test_type,
                                    )
                                )
                            elif function == def_name.name:
                                test_functions.add(
                                    TestFunction(
                                        function_name=def_name.name,
                                        test_class=matched_name,
                                        parameters=None,
                                        test_type=functions[0].test_type,
                                    )
                                )

        test_functions_by_name = defaultdict(list)
        for func in test_functions:
            test_functions_by_name[func.function_name].append(func)

        test_function_names_set = set(test_functions_by_name.keys())
        relevant_names = []

        names_with_full_name = [
            name for name in all_names if name.full_name is not None
        ]

        for name in names_with_full_name:
            match = FUNCTION_NAME_REGEX.search(name.full_name)
            if match and match.group(1) in test_function_names_set:
                relevant_names.append((name, match.group(1)))

        for name, scope in relevant_names:
            try:
                definition = name.goto(
                    follow_imports=True, follow_builtin_imports=False
                )
            except Exception as e:
                logger.debug(str(e))
                continue
            try:
                if not definition or definition[0].type != "function":
                    # Fallback: Try to match against functions_to_optimize when Jedi can't resolve
                    # This handles cases where Jedi fails with pytest fixtures
                    if functions_to_optimize and name.name:
                        for func_to_opt in functions_to_optimize:
                            # Check if this unresolved name matches a function we're looking for
                            if func_to_opt.function_name == name.name:
                                # Check if the test file imports the class/module containing this function
                                qualified_name_with_modules = (
                                    func_to_opt.qualified_name_with_modules_from_root(
                                        project_root_path
                                    )
                                )

                                # Only add if this test actually tests the function we're optimizing
                                for test_func in test_functions_by_name[scope]:
                                    if test_func.parameters is not None:
                                        if test_framework == "pytest":
                                            scope_test_function = f"{test_func.function_name}[{test_func.parameters}]"
                                        else:  # unittest
                                            scope_test_function = f"{test_func.function_name}_{test_func.parameters}"
                                    else:
                                        scope_test_function = test_func.function_name

                                    function_to_test_map[
                                        qualified_name_with_modules
                                    ].add(
                                        FunctionCalledInTest(
                                            tests_in_file=TestsInFile(
                                                test_file=test_file,
                                                test_class=test_func.test_class,
                                                test_function=scope_test_function,
                                                test_type=test_func.test_type,
                                            ),
                                            position=CodePosition(
                                                line_no=name.line,
                                                col_no=name.column,
                                            ),
                                        )
                                    )
                                    tests_cache.insert_test(
                                        file_path=str(test_file),
                                        file_hash=file_hash,
                                        qualified_name_with_modules_from_root=qualified_name_with_modules,
                                        function_name=scope,
                                        test_class=test_func.test_class or "",
                                        test_function=scope_test_function,
                                        test_type=test_func.test_type,
                                        line_number=name.line,
                                        col_number=name.column,
                                    )

                                    if test_func.test_type == TestType.REPLAY_TEST:
                                        num_discovered_replay_tests += 1

                                    num_discovered_tests += 1
                    continue
                definition_obj = definition[0]
                definition_path = str(definition_obj.module_path)

                project_root_str = str(project_root_path)
                if (
                    definition_path.startswith(project_root_str + os.sep)
                    and definition_obj.module_name != name.module_name
                    and definition_obj.full_name is not None
                ):
                    # Pre-compute common values outside the inner loop
                    module_prefix = definition_obj.module_name + "."
                    full_name_without_module_prefix = definition_obj.full_name.replace(
                        module_prefix, "", 1
                    )
                    qualified_name_with_modules_from_root = f"{module_name_from_file_path(definition_obj.module_path, project_root_path)}.{full_name_without_module_prefix}"

                    for test_func in test_functions_by_name[scope]:
                        if test_func.parameters is not None:
                            if test_framework == "pytest":
                                scope_test_function = (
                                    f"{test_func.function_name}[{test_func.parameters}]"
                                )
                            else:  # unittest
                                scope_test_function = (
                                    f"{test_func.function_name}_{test_func.parameters}"
                                )
                        else:
                            scope_test_function = test_func.function_name

                        function_to_test_map[qualified_name_with_modules_from_root].add(
                            FunctionCalledInTest(
                                tests_in_file=TestsInFile(
                                    test_file=test_file,
                                    test_class=test_func.test_class,
                                    test_function=scope_test_function,
                                    test_type=test_func.test_type,
                                ),
                                position=CodePosition(
                                    line_no=name.line, col_no=name.column
                                ),
                            )
                        )
                        tests_cache.insert_test(
                            file_path=str(test_file),
                            file_hash=file_hash,
                            qualified_name_with_modules_from_root=qualified_name_with_modules_from_root,
                            function_name=scope,
                            test_class=test_func.test_class or "",
                            test_function=scope_test_function,
                            test_type=test_func.test_type,
                            line_number=name.line,
                            col_number=name.column,
                        )

                        if test_func.test_type == TestType.REPLAY_TEST:
                            num_discovered_replay_tests += 1

                        num_discovered_tests += 1
            except Exception as e:
                logger.debug(str(e))
                continue

    tests_cache.close()

    return dict(function_to_test_map), num_discovered_tests, num_discovered_replay_tests
