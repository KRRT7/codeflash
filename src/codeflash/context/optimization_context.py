from __future__ import annotations

import hashlib
from itertools import chain
from typing import TYPE_CHECKING

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.code_extractor import find_preexisting_objects
from codeflash.code_utils.config_consts import (
    OPTIMIZATION_CONTEXT_TOKEN_LIMIT,
    TESTGEN_CONTEXT_TOKEN_LIMIT,
)
from codeflash.code_utils.diff_utils import encoded_tokens_len
from codeflash.context.code_context_extractor import (
    extract_code_markdown_context_from_files,
    get_function_sources_from_jedi,
    get_function_to_optimize_as_function_source,
    get_imported_class_definitions,
)
from codeflash.models.domain import (
    CodeContextType,
    CodeOptimizationContext,
    CodeStringsMarkdown,
)

if TYPE_CHECKING:
    from pathlib import Path

    from codeflash.discovery.functions_to_optimize import FunctionToOptimize


def get_code_optimization_context(
    function_to_optimize: FunctionToOptimize,
    project_root_path: Path,
    optim_token_limit: int = OPTIMIZATION_CONTEXT_TOKEN_LIMIT,
    testgen_token_limit: int = TESTGEN_CONTEXT_TOKEN_LIMIT,
) -> CodeOptimizationContext:
    helpers_of_fto_dict, helpers_of_fto_list = get_function_sources_from_jedi(
        {function_to_optimize.file_path: {function_to_optimize.qualified_name}},
        project_root_path,
    )

    fto_as_function_source = get_function_to_optimize_as_function_source(
        function_to_optimize, project_root_path
    )
    helpers_of_fto_dict[function_to_optimize.file_path].add(fto_as_function_source)

    helpers_of_fto_qualified_names_dict = {
        file_path: {source.qualified_name for source in sources}
        for file_path, sources in helpers_of_fto_dict.items()
    }

    for qualified_names in helpers_of_fto_qualified_names_dict.values():
        qualified_names.update(
            {f"{qn.rsplit('.', 1)[0]}.__init__" for qn in qualified_names if "." in qn}
        )

    helpers_of_helpers_dict, helpers_of_helpers_list = get_function_sources_from_jedi(
        helpers_of_fto_qualified_names_dict, project_root_path
    )

    final_read_writable_code = extract_code_markdown_context_from_files(
        helpers_of_fto_dict,
        {},
        project_root_path,
        remove_docstrings=False,
        code_context_type=CodeContextType.READ_WRITABLE,
    )

    read_only_code_markdown = extract_code_markdown_context_from_files(
        helpers_of_fto_dict,
        helpers_of_helpers_dict,
        project_root_path,
        remove_docstrings=False,
        code_context_type=CodeContextType.READ_ONLY,
    )
    hashing_code_context = extract_code_markdown_context_from_files(
        helpers_of_fto_dict,
        helpers_of_helpers_dict,
        project_root_path,
        remove_docstrings=True,
        code_context_type=CodeContextType.HASHING,
    )

    final_read_writable_tokens = encoded_tokens_len(final_read_writable_code.markdown)
    if final_read_writable_tokens > optim_token_limit:
        raise ValueError("Read-writable code has exceeded token limit, cannot proceed")

    preexisting_objects = set(
        chain(
            *(
                find_preexisting_objects(codestring.code)
                for codestring in final_read_writable_code.code_strings
            ),
            *(
                find_preexisting_objects(codestring.code)
                for codestring in read_only_code_markdown.code_strings
            ),
        )
    )
    read_only_context_code = read_only_code_markdown.markdown

    read_only_code_markdown_tokens = encoded_tokens_len(read_only_context_code)
    total_tokens = final_read_writable_tokens + read_only_code_markdown_tokens
    if total_tokens > optim_token_limit:
        logger.debug(
            "Code context has exceeded token limit, removing docstrings from read-only code"
        )
        read_only_code_no_docstring_markdown = extract_code_markdown_context_from_files(
            helpers_of_fto_dict,
            helpers_of_helpers_dict,
            project_root_path,
            remove_docstrings=True,
        )
        read_only_context_code = read_only_code_no_docstring_markdown.markdown
        read_only_code_no_docstring_markdown_tokens = encoded_tokens_len(
            read_only_context_code
        )
        total_tokens = (
            final_read_writable_tokens + read_only_code_no_docstring_markdown_tokens
        )
        if total_tokens > optim_token_limit:
            logger.debug(
                "Code context has exceeded token limit, removing read-only code"
            )
            read_only_context_code = ""

    testgen_context = extract_code_markdown_context_from_files(
        helpers_of_fto_dict,
        helpers_of_helpers_dict,
        project_root_path,
        remove_docstrings=False,
        code_context_type=CodeContextType.TESTGEN,
    )

    imported_class_context = get_imported_class_definitions(
        testgen_context, project_root_path
    )
    if imported_class_context.code_strings:
        testgen_context = CodeStringsMarkdown(
            code_strings=testgen_context.code_strings
            + imported_class_context.code_strings
        )

    testgen_markdown_code = testgen_context.markdown
    testgen_code_token_length = encoded_tokens_len(testgen_markdown_code)
    if testgen_code_token_length > testgen_token_limit:
        testgen_context = extract_code_markdown_context_from_files(
            helpers_of_fto_dict,
            helpers_of_helpers_dict,
            project_root_path,
            remove_docstrings=True,
            code_context_type=CodeContextType.TESTGEN,
        )
        imported_class_context = get_imported_class_definitions(
            testgen_context, project_root_path
        )
        if imported_class_context.code_strings:
            testgen_context = CodeStringsMarkdown(
                code_strings=testgen_context.code_strings
                + imported_class_context.code_strings
            )
        testgen_markdown_code = testgen_context.markdown
        testgen_code_token_length = encoded_tokens_len(testgen_markdown_code)
        if testgen_code_token_length > testgen_token_limit:
            testgen_context = extract_code_markdown_context_from_files(
                helpers_of_fto_dict,
                helpers_of_helpers_dict,
                project_root_path,
                remove_docstrings=True,
                code_context_type=CodeContextType.TESTGEN,
            )
            testgen_markdown_code = testgen_context.markdown
            testgen_code_token_length = encoded_tokens_len(testgen_markdown_code)
            if testgen_code_token_length > testgen_token_limit:
                raise ValueError(
                    "Testgen code context has exceeded token limit, cannot proceed"
                )
    code_hash_context = hashing_code_context.markdown
    code_hash = hashlib.sha256(code_hash_context.encode("utf-8")).hexdigest()

    return CodeOptimizationContext(
        testgen_context=testgen_context,
        read_writable_code=final_read_writable_code,
        read_only_context_code=read_only_context_code,
        hashing_code_context=code_hash_context,
        hashing_code_context_hash=code_hash,
        helper_functions=helpers_of_fto_list,
        preexisting_objects=preexisting_objects,
    )
