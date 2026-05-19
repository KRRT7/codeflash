from __future__ import annotations

import sys

from codeflash.code_utils.cleanup import (
    cleanup_paths,
    get_run_tmp_file,
    restore_conftest,
)
from codeflash.code_utils.config_utils import (
    ImportErrorPattern,
    add_addopts_to_pyproject,
    custom_addopts,
    filter_args,
    get_qualified_name,
    modify_addopts,
)
from codeflash.code_utils.diff_utils import (
    choose_weights,
    create_rank_dictionary_compact,
    create_score_dictionary_from_metrics,
    diff_length,
    encoded_tokens_len,
    normalize_by_max,
    unified_diff_strings,
)
from codeflash.code_utils.path_utils import (
    file_name_from_test_module_name,
    file_path_from_module_name,
    module_name_from_file_path,
    path_belongs_to_site_packages,
    validate_relative_directory_path,
)
from codeflash.code_utils.pytest_utils import (
    extract_unique_errors,
    shorten_pytest_error,
)
from codeflash.code_utils.validation import (
    get_all_function_names,
    get_imports_from_file,
    is_class_defined_in_file,
    validate_python_code,
)


def exit_with_message(message: str, *, error_on_exit: bool = False) -> None:
    print(message)
    sys.exit(1 if error_on_exit else 0)
