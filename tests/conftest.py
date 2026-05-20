from __future__ import annotations

import os
from pathlib import Path

import pytest

from codeflash.models.domain import TestFile, TestFiles, TestType
from codeflash.verification.verification_utils import TestConfig


@pytest.fixture
def tests_root() -> Path:
    return Path(__file__).parent.resolve()


@pytest.fixture
def project_root(tests_root: Path) -> Path:
    return tests_root.parent


@pytest.fixture
def test_config(tests_root: Path, project_root: Path) -> TestConfig:
    return TestConfig(
        tests_root=tests_root,
        project_root_path=tests_root,
        tests_project_rootdir=project_root,
        test_framework="pytest",
    )


@pytest.fixture
def test_env(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CODEFLASH_TEST_ITERATION"] = "0"
    env["CODEFLASH_TRACER_DISABLE"] = "1"
    if "PYTHONPATH" not in env:
        env["PYTHONPATH"] = str(project_root)
    else:
        env["PYTHONPATH"] += os.pathsep + str(project_root)
    return env


def make_test_files(
    behavior_path: Path | None = None,
    benchmark_path: Path | None = None,
    test_type: TestType = TestType.EXISTING_UNIT_TEST,
) -> TestFiles:
    """Helper to create a TestFiles instance with a single test file."""
    test_files = TestFiles(test_files=[])
    if behavior_path:
        test_files.add(
            TestFile(
                instrumented_behavior_file_path=behavior_path,
                test_type=test_type,
            )
        )
    if benchmark_path:
        test_files.add(
            TestFile(
                instrumented_behavior_file_path=behavior_path or benchmark_path,
                benchmarking_file_path=benchmark_path,
                test_type=test_type,
            )
        )
    return test_files
