from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import dill as pickle

from codeflash.cli_cmds.logging_config import logger, rule
from codeflash.code_utils.path_utils import file_path_from_module_name
from codeflash._constants import VerificationType
from codeflash.models.invocation_id import (
    FunctionTestInvocation,
    InvocationId,
)
from codeflash.models.test_results import TestResults
from codeflash.models.test_type import TestType

if TYPE_CHECKING:
    from codeflash.models.domain import TestFiles
    from codeflash.verification.verification_utils import TestConfig


def parse_sqlite_test_results(
    sqlite_file_path: Path, test_files: TestFiles, test_config: TestConfig
) -> TestResults:
    test_results = TestResults()
    if not sqlite_file_path.exists():
        logger.warning(f"No test results for {sqlite_file_path} found.")
        rule()
        return test_results
    db = None
    try:
        db = sqlite3.connect(sqlite_file_path)
        cur = db.cursor()
        data = cur.execute(
            "SELECT test_module_path, test_class_name, test_function_name, "
            "function_getting_tested, loop_index, iteration_id, runtime, return_value,verification_type FROM test_results"
        ).fetchall()
    except Exception as e:
        logger.warning(
            f"Failed to parse test results from {sqlite_file_path}. Exception: {e}"
        )
        if db is not None:
            db.close()
        return test_results
    finally:
        if db is not None:
            db.close()
    for val in data:
        try:
            test_module_path = val[0]
            test_class_name = val[1] if val[1] else None
            test_function_name = val[2] if val[2] else None
            function_getting_tested = val[3]
            test_file_path = file_path_from_module_name(
                test_module_path, test_config.tests_project_rootdir
            )
            loop_index = val[4]
            iteration_id = val[5]
            runtime = val[6]
            verification_type = val[8]
            if verification_type in {
                VerificationType.INIT_STATE_FTO,
                VerificationType.INIT_STATE_HELPER,
            }:
                test_type = TestType.INIT_STATE_TEST
            else:
                test_type = (
                    test_files.get_test_type_by_original_file_path(test_file_path)
                    or TestType.INIT_STATE_TEST
                )
            try:
                ret_val = (pickle.loads(val[7]) if loop_index == 1 else None,)
            except Exception:
                continue
            test_results.add(
                function_test_invocation=FunctionTestInvocation(
                    loop_index=loop_index,
                    id=InvocationId(
                        test_module_path=test_module_path,
                        test_class_name=test_class_name,
                        test_function_name=test_function_name,
                        function_getting_tested=function_getting_tested,
                        iteration_id=iteration_id,
                    ),
                    file_name=test_file_path,
                    did_pass=True,
                    runtime=runtime,
                    test_framework=test_config.test_framework,
                    test_type=test_type,
                    return_value=ret_val,
                    timed_out=False,
                    verification_type=VerificationType(verification_type)
                    if verification_type
                    else None,
                )
            )
        except Exception:
            logger.exception(
                f"Failed to parse sqlite test results for {sqlite_file_path}"
            )
    return test_results
