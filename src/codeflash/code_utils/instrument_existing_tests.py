from __future__ import annotations
from codeflash.code_utils.path_utils import module_name_from_file_path

import ast
from pathlib import Path

from codeflash.discovery.functions_to_optimize import FunctionToOptimize
from codeflash.models.domain import CodePosition
from codeflash.models.coverage import TestingMode

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.formatter import sort_imports
from codeflash.code_utils.wrapper_gen import create_wrapper_function
from codeflash.code_utils.async_instrumentation import (
    FunctionImportedAsVisitor,
    inject_async_profiling_into_existing_test,
)
from codeflash.code_utils.sync_instrumentation import (
    InjectPerfOnly,
)


def detect_frameworks_from_code(code: str) -> dict[str, str]:
    """Detect GPU/device frameworks (torch, tensorflow, jax) used in the code by analyzing imports.

    Returns:
        A dictionary mapping framework names to their import aliases.
        For example: {"torch": "th", "tensorflow": "tf", "jax": "jax"}

    """
    frameworks: dict[str, str] = {}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return frameworks

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split(".")[0]
                if module_name == "torch":
                    # Use asname if available, otherwise use the module name
                    frameworks["torch"] = alias.asname if alias.asname else module_name
                elif module_name == "tensorflow":
                    frameworks["tensorflow"] = (
                        alias.asname if alias.asname else module_name
                    )
                elif module_name == "jax":
                    frameworks["jax"] = alias.asname if alias.asname else module_name
        elif isinstance(node, ast.ImportFrom):  # noqa: SIM102
            if node.module:
                module_name = node.module.split(".")[0]
                if module_name == "torch" and "torch" not in frameworks:
                    frameworks["torch"] = module_name
                elif module_name == "tensorflow" and "tensorflow" not in frameworks:
                    frameworks["tensorflow"] = module_name
                elif module_name == "jax" and "jax" not in frameworks:
                    frameworks["jax"] = module_name

    return frameworks


def inject_profiling_into_existing_test(
    test_path: Path,
    call_positions: list[CodePosition],
    function_to_optimize: FunctionToOptimize,
    tests_project_root: Path,
    mode: TestingMode = TestingMode.BEHAVIOR,
) -> tuple[bool, str | None]:
    if function_to_optimize.is_async:
        return inject_async_profiling_into_existing_test(
            test_path, call_positions, function_to_optimize, tests_project_root, mode
        )

    with test_path.open(encoding="utf8") as f:
        test_code = f.read()

    used_frameworks = detect_frameworks_from_code(test_code)
    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        logger.exception(f"Syntax error in code in file - {test_path}")
        return False, None

    test_module_path = module_name_from_file_path(test_path, tests_project_root)
    import_visitor = FunctionImportedAsVisitor(function_to_optimize)
    import_visitor.visit(tree)
    func = import_visitor.imported_as

    tree = InjectPerfOnly(func, test_module_path, call_positions, mode=mode).visit(tree)
    new_imports = [
        ast.Import(names=[ast.alias(name="time")]),
        ast.Import(names=[ast.alias(name="gc")]),
        ast.Import(names=[ast.alias(name="os")]),
    ]
    if mode == TestingMode.BEHAVIOR:
        new_imports.extend(
            [
                ast.Import(names=[ast.alias(name="inspect")]),
                ast.Import(names=[ast.alias(name="sqlite3")]),
                ast.Import(names=[ast.alias(name="dill", asname="pickle")]),
            ]
        )
    # Add framework imports for GPU sync code (needed when framework is only imported via submodule)
    for framework_name, framework_alias in used_frameworks.items():
        if framework_alias == framework_name:
            # Only add import if we're using the framework name directly (not an alias)
            # This handles cases like "from torch.nn import Module" where torch needs to be imported
            new_imports.append(ast.Import(names=[ast.alias(name=framework_name)]))
        else:
            # If there's an alias, use it (e.g., "import torch as th")
            new_imports.append(
                ast.Import(
                    names=[ast.alias(name=framework_name, asname=framework_alias)]
                )
            )
    additional_functions = [create_wrapper_function(mode, used_frameworks)]

    tree.body = [*new_imports, *additional_functions, *tree.body]
    return True, sort_imports(ast.unparse(tree), float_to_top=True)
