from __future__ import annotations

import ast
from pathlib import Path

from codeflash.cli_cmds.logging_config import logger


def validate_python_code(code: str) -> str:
    try:
        compile(code, "<string>", "exec")
    except SyntaxError as e:
        msg = f"Invalid Python code: {e.msg} (line {e.lineno}, column {e.offset})"
        raise ValueError(msg) from e
    return code


def get_imports_from_file(
    file_path: Path | None = None,
    file_string: str | None = None,
    file_ast: ast.AST | None = None,
) -> list[ast.Import | ast.ImportFrom]:
    assert (
        sum([file_path is not None, file_string is not None, file_ast is not None]) == 1
    ), "Must provide exactly one of file_path, file_string, or file_ast"
    if file_path:
        with file_path.open(encoding="utf8") as file:
            file_string = file.read()
    if file_ast is None:
        if file_string is None:
            logger.error("file_string cannot be None when file_ast is not provided")
            return []
        try:
            file_ast = ast.parse(file_string)
        except SyntaxError as e:
            logger.exception(f"Syntax error in code: {e}")
            return []
    return [
        node
        for node in ast.walk(file_ast)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def get_all_function_names(code: str) -> tuple[bool, list[str]]:
    try:
        module = ast.parse(code)
    except SyntaxError as e:
        logger.exception(f"Syntax error in code: {e}")
        return False, []
    function_names = [
        node.name
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return True, function_names


def is_class_defined_in_file(class_name: str, file_path: Path) -> bool:
    if not file_path.exists():
        return False
    with file_path.open(encoding="utf8") as file:
        source = file.read()
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.ClassDef) and node.name == class_name
        for node in ast.walk(tree)
    )
