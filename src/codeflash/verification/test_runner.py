from __future__ import annotations

import contextlib
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from codeflash.cli_cmds.console import logger
from codeflash.code_utils.code_utils import custom_addopts, get_run_tmp_file
from codeflash.code_utils.compat import IS_POSIX, SAFE_SYS_EXECUTABLE
from codeflash.code_utils.config_consts import TOTAL_LOOPING_TIME_EFFECTIVE
from codeflash.code_utils.coverage_utils import prepare_coverage_files
from codeflash.code_utils.shell_utils import get_cross_platform_subprocess_run_args
from codeflash.models.models import TestFiles, TestType

if TYPE_CHECKING:
    from codeflash.models.models import TestFiles

BEHAVIORAL_BLOCKLISTED_PLUGINS = ["benchmark", "codspeed", "xdist", "sugar"]
BENCHMARKING_BLOCKLISTED_PLUGINS = [
    "codspeed",
    "cov",
    "benchmark",
    "profiling",
    "xdist",
    "sugar",
]


@dataclass
class PytestRunResult:
    result_file_path: Path
    run_result: subprocess.CompletedProcess
    coverage_database_file: Path | None = None
    coverage_config_file: Path | None = None


def execute_test_subprocess(
    cmd_list: list[str], cwd: Path, env: dict[str, str] | None, timeout: int = 600
) -> subprocess.CompletedProcess:
    """Execute a subprocess with the given command list, working directory, environment variables, and timeout."""
    logger.debug(f"executing test run with command: {' '.join(cmd_list)}")
    with custom_addopts():
        run_args = get_cross_platform_subprocess_run_args(
            cwd=cwd,
            env=env,
            timeout=timeout,
            check=False,
            text=True,
            capture_output=True,
        )
        return subprocess.run(cmd_list, **run_args)  # noqa: PLW1510


def run_pytest_tests(
    test_paths: TestFiles,
    test_framework: str,
    test_env: dict[str, str],
    cwd: Path,
    *,
    pytest_timeout: int | None = None,
    pytest_cmd: str = "pytest",
    pytest_target_runtime_seconds: float = TOTAL_LOOPING_TIME_EFFECTIVE,
    pytest_min_loops: int = 1,
    pytest_max_loops: int = 1,
    enable_coverage: bool = False,
    enable_line_profile: bool = False,
    enable_stability_check: bool = False,
    use_benchmarking_files: bool = False,
) -> PytestRunResult:
    """Run pytest tests with configurable mode.

    Args:
        test_paths: Test files to run.
        test_framework: Test framework (pytest or unittest).
        test_env: Environment variables for the test run.
        cwd: Working directory.
        pytest_timeout: Timeout for each test.
        pytest_cmd: Pytest command to use.
        pytest_target_runtime_seconds: Target runtime for each test.
        pytest_min_loops: Minimum loops for benchmarking.
        pytest_max_loops: Maximum loops for benchmarking.
        enable_coverage: Enable coverage collection.
        enable_line_profile: Enable line profiling mode (sets LINE_PROFILE=1).
        enable_stability_check: Enable stability check (--codeflash_stability_check=true).
        use_benchmarking_files: Use benchmarking_file_path instead of instrumented_behavior_file_path.

    Returns:
        PytestRunResult with result file path, subprocess result, and optional coverage files.

    """
    if test_framework not in {"pytest", "unittest"}:
        msg = f"Unsupported test framework: {test_framework}"
        raise ValueError(msg)

    if use_benchmarking_files:
        test_files: list[str] = list(
            {str(file.benchmarking_file_path) for file in test_paths.test_files}
        )
    else:
        test_files = []
        for file in test_paths.test_files:
            if file.test_type == TestType.REPLAY_TEST:
                if file.tests_in_file:
                    test_files.extend(
                        str(file.instrumented_behavior_file_path)
                        + "::"
                        + test.test_function
                        for test in file.tests_in_file
                    )
            else:
                test_files.append(str(file.instrumented_behavior_file_path))
        test_files = list(set(test_files))

    pytest_cmd_list = (
        shlex.split(f"{SAFE_SYS_EXECUTABLE} -m pytest", posix=IS_POSIX)
        if pytest_cmd == "pytest"
        else [SAFE_SYS_EXECUTABLE, "-m", *shlex.split(pytest_cmd, posix=IS_POSIX)]
    )

    pytest_args = [
        "--capture=tee-sys",
        "-q",
        "--codeflash_loops_scope=session",
        f"--codeflash_min_loops={pytest_min_loops}",
        f"--codeflash_max_loops={pytest_max_loops}",
        f"--codeflash_seconds={pytest_target_runtime_seconds}",
    ]
    if enable_stability_check:
        pytest_args.append("--codeflash_stability_check=true")
    if pytest_timeout is not None:
        pytest_args.append(f"--timeout={pytest_timeout}")

    result_file_path = get_run_tmp_file(Path("pytest_results.xml"))
    result_args = [
        f"--junitxml={result_file_path.as_posix()}",
        "-o",
        "junit_logging=all",
    ]

    pytest_test_env = test_env.copy()
    pytest_test_env["PYTEST_PLUGINS"] = "codeflash.verification.pytest_plugin"

    if enable_line_profile:
        pytest_test_env["LINE_PROFILE"] = "1"

    coverage_database_file = None
    coverage_config_file = None

    if enable_coverage:
        coverage_database_file, coverage_config_file = prepare_coverage_files()
        pytest_test_env["NUMBA_DISABLE_JIT"] = str(1)
        pytest_test_env["TORCHDYNAMO_DISABLE"] = str(1)
        pytest_test_env["PYTORCH_JIT"] = str(0)
        pytest_test_env["TF_XLA_FLAGS"] = "--tf_xla_auto_jit=0"
        pytest_test_env["TF_ENABLE_ONEDNN_OPTS"] = str(0)
        pytest_test_env["JAX_DISABLE_JIT"] = str(0)

        is_windows = sys.platform == "win32"
        if is_windows:
            if coverage_database_file.exists():
                with contextlib.suppress(PermissionError, OSError):
                    coverage_database_file.unlink()
        else:
            cov_erase = execute_test_subprocess(
                shlex.split(f"{SAFE_SYS_EXECUTABLE} -m coverage erase"),
                cwd=cwd,
                env=pytest_test_env,
                timeout=30,
            )
            logger.debug(cov_erase)

        coverage_cmd = [
            SAFE_SYS_EXECUTABLE,
            "-m",
            "coverage",
            "run",
            f"--rcfile={coverage_config_file.as_posix()}",
            "-m",
        ]
        if pytest_cmd == "pytest":
            coverage_cmd.extend(["pytest"])
        else:
            coverage_cmd.extend(shlex.split(pytest_cmd, posix=IS_POSIX)[1:])

        blocklist_args = [
            f"-p no:{plugin}"
            for plugin in BEHAVIORAL_BLOCKLISTED_PLUGINS
            if plugin != "cov"
        ]
        results = execute_test_subprocess(
            coverage_cmd + pytest_args + blocklist_args + result_args + test_files,
            cwd=cwd,
            env=pytest_test_env,
            timeout=600,
        )
    else:
        blocklist_args = [
            f"-p no:{plugin}"
            for plugin in (
                BENCHMARKING_BLOCKLISTED_PLUGINS
                if use_benchmarking_files
                else BEHAVIORAL_BLOCKLISTED_PLUGINS
            )
        ]
        results = execute_test_subprocess(
            pytest_cmd_list + pytest_args + blocklist_args + result_args + test_files,
            cwd=cwd,
            env=pytest_test_env,
            timeout=600,
        )

    logger.debug(
        f"Result return code: {results.returncode}, "
        f"{'Result stderr:' + str(results.stderr) if results.stderr else ''}"
    )

    return PytestRunResult(
        result_file_path=result_file_path,
        run_result=results,
        coverage_database_file=coverage_database_file,
        coverage_config_file=coverage_config_file,
    )
