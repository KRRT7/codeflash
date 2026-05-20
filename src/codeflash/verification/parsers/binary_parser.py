from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import dill as pickle

from codeflash.cli_cmds.logging_config import DEBUG_MODE, logger, rule
from codeflash.code_utils.path_utils import file_path_from_module_name
from codeflash._constants import VerificationType
from codeflash.models.invocation_id import (
    FunctionTestInvocation,
    InvocationId,
)
from codeflash.models.test_results import TestResults

if TYPE_CHECKING:
    from codeflash.models.domain import TestFiles
    from codeflash.verification.verification_utils import TestConfig


def parse_test_return_values_bin(
    file_location: Path, test_files: TestFiles, test_config: TestConfig
) -> TestResults:
    test_results = TestResults()
    if not file_location.exists():
        logger.debug(f"No test results for {file_location} found.")
        rule()
        return test_results

    with file_location.open("rb") as file:
        try:
            while file:
                len_next_bytes = file.read(4)
                if not len_next_bytes:
                    return test_results
                len_next = int.from_bytes(len_next_bytes, byteorder="big")
                encoded_test_bytes = file.read(len_next)
                encoded_test_name = encoded_test_bytes.decode("ascii")
                duration_bytes = file.read(8)
                duration = int.from_bytes(duration_bytes, byteorder="big")
                len_next_bytes = file.read(4)
                len_next = int.from_bytes(len_next_bytes, byteorder="big")
                test_pickle_bin = file.read(len_next)
                loop_index_bytes = file.read(8)
                loop_index = int.from_bytes(loop_index_bytes, byteorder="big")
                len_next_bytes = file.read(4)
                len_next = int.from_bytes(len_next_bytes, byteorder="big")
                invocation_id_bytes = file.read(len_next)
                invocation_id = invocation_id_bytes.decode("ascii")

                invocation_id_object = InvocationId.from_str_id(
                    encoded_test_name, invocation_id
                )
                test_file_path = file_path_from_module_name(
                    invocation_id_object.test_module_path,
                    test_config.tests_project_rootdir,
                )

                test_type = test_files.get_test_type_by_instrumented_file_path(
                    test_file_path
                )
                try:
                    test_pickle = (
                        pickle.loads(test_pickle_bin) if loop_index == 1 else None
                    )
                except Exception as e:
                    if DEBUG_MODE:
                        logger.exception(
                            f"Failed to load pickle file for {encoded_test_name} Exception: {e}"
                        )
                    continue
                assert test_type is not None, (
                    f"Test type not found for {test_file_path}"
                )
                test_results.add(
                    function_test_invocation=FunctionTestInvocation(
                        loop_index=loop_index,
                        id=invocation_id_object,
                        file_name=test_file_path,
                        did_pass=True,
                        runtime=duration,
                        test_framework=test_config.test_framework,
                        test_type=test_type,
                        return_value=test_pickle,
                        timed_out=False,
                        verification_type=VerificationType.FUNCTION_CALL,
                    )
                )
        except Exception as e:
            logger.warning(
                f"Failed to parse test results from {file_location}. Exception: {e}"
            )
            return test_results
    return test_results
