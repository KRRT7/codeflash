from __future__ import annotations
from codeflash.code_utils.validation import is_class_defined_in_file
from codeflash.code_utils.path_utils import (
    module_name_from_file_path,
)

import ast
import random
import warnings
from _ast import AsyncFunctionDef, ClassDef, FunctionDef
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import libcst as cst
from pydantic.dataclasses import dataclass

from codeflash.cli_cmds.logging_config import DEBUG_MODE, logger, rule
from codeflash.code_utils.code_utils import exit_with_message
from codeflash.code_utils.git_utils import get_git_diff
from codeflash.discovery.discover_unit_tests import discover_unit_tests
from codeflash.discovery.function_filter import filter_functions
from codeflash.models.domain import FunctionParent

if TYPE_CHECKING:
    from libcst import CSTNode
    from libcst.metadata import CodeRange

    from codeflash.verification.verification_utils import TestConfig

_property_id = "property"

_ast_name = ast.Name


@dataclass(frozen=True)
class FunctionProperties:
    is_top_level: bool
    has_args: bool | None
    is_staticmethod: bool | None
    is_classmethod: bool | None
    staticmethod_class_name: str | None


class ReturnStatementVisitor(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.has_return_statement: bool = False

    def visit_Return(self, node: cst.Return) -> None:  # noqa: ARG002
        self.has_return_statement = True


class FunctionVisitor(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (
        cst.metadata.PositionProvider,
        cst.metadata.ParentNodeProvider,
    )

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path: str = file_path
        self.functions: list[FunctionToOptimize] = []

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        return_visitor: ReturnStatementVisitor = ReturnStatementVisitor()
        node.visit(return_visitor)
        if return_visitor.has_return_statement:
            pos: CodeRange = self.get_metadata(cst.metadata.PositionProvider, node)
            parents: CSTNode | None = self.get_metadata(
                cst.metadata.ParentNodeProvider, node
            )
            ast_parents: list[FunctionParent] = []
            while parents is not None:
                if isinstance(parents, (cst.FunctionDef, cst.ClassDef)):
                    ast_parents.append(
                        FunctionParent(parents.name.value, parents.__class__.__name__)
                    )
                parents = self.get_metadata(
                    cst.metadata.ParentNodeProvider, parents, default=None
                )
            self.functions.append(
                FunctionToOptimize(
                    function_name=node.name.value,
                    file_path=self.file_path,  # type: ignore[arg-type]
                    parents=list(reversed(ast_parents)),
                    starting_line=pos.start.line,
                    ending_line=pos.end.line,
                    is_async=bool(node.asynchronous),
                )
            )


class FunctionWithReturnStatement(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.functions: list[FunctionToOptimize] = []
        self.ast_path: list[FunctionParent] = []
        self.file_path: Path = file_path

    def visit_FunctionDef(self, node: FunctionDef) -> None:
        # Check if the function has a return statement and add it to the list
        if function_has_return_statement(node) and not function_is_a_property(node):
            self.functions.append(
                FunctionToOptimize(
                    function_name=node.name,
                    file_path=self.file_path,
                    parents=self.ast_path[:],
                )
            )

    def visit_AsyncFunctionDef(self, node: AsyncFunctionDef) -> None:
        # Check if the async function has a return statement and add it to the list
        if function_has_return_statement(node) and not function_is_a_property(node):
            self.functions.append(
                FunctionToOptimize(
                    function_name=node.name,
                    file_path=self.file_path,
                    parents=self.ast_path[:],
                    is_async=True,
                )
            )

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, (FunctionDef, AsyncFunctionDef, ClassDef)):
            self.ast_path.append(FunctionParent(node.name, node.__class__.__name__))
        super().generic_visit(node)
        if isinstance(node, (FunctionDef, AsyncFunctionDef, ClassDef)):
            self.ast_path.pop()


@dataclass(frozen=True, config={"arbitrary_types_allowed": True})
class FunctionToOptimize:
    """Represent a function that is a candidate for optimization.

    Attributes
    ----------
        function_name: The name of the function.
        file_path: The absolute file path where the function is located.
        parents: A list of parent scopes, which could be classes or functions.
        starting_line: The starting line number of the function in the file.
        ending_line: The ending line number of the function in the file.
        is_async: Whether this function is defined as async.

    The qualified_name property provides the full name of the function, including
    any parent class or function names. The qualified_name_with_modules_from_root
    method extends this with the module name from the project root.

    """

    function_name: str
    file_path: Path
    parents: list[FunctionParent]  # list[ClassDef | FunctionDef | AsyncFunctionDef]
    starting_line: int | None = None
    ending_line: int | None = None
    is_async: bool = False

    @property
    def top_level_parent_name(self) -> str:
        return self.function_name if not self.parents else self.parents[0].name

    def __str__(self) -> str:
        return (
            f"{self.file_path}:{'.'.join([p.name for p in self.parents])}"
            f"{'.' if self.parents else ''}{self.function_name}"
        )

    @property
    def qualified_name(self) -> str:
        if not self.parents:
            return self.function_name
        # Join all parent names with dots to handle nested classes properly
        parent_path = ".".join(parent.name for parent in self.parents)
        return f"{parent_path}.{self.function_name}"

    def qualified_name_with_modules_from_root(self, project_root_path: Path) -> str:
        return f"{module_name_from_file_path(self.file_path, project_root_path)}.{self.qualified_name}"


def get_functions_to_optimize(
    optimize_all: str | None,
    replay_test: list[Path] | None,
    file: Path | str | None,
    only_get_this_function: str | None,
    test_cfg: TestConfig,
    ignore_paths: list[Path],
    project_root: Path,
    module_root: Path,
) -> tuple[dict[Path, list[FunctionToOptimize]], int, Path | None]:
    assert sum([bool(optimize_all), bool(replay_test), bool(file)]) <= 1, (
        "Only one of optimize_all, replay_test, or file should be provided"
    )
    functions: dict[str, list[FunctionToOptimize]]
    trace_file_path: Path | None = None
    with warnings.catch_warnings():
        warnings.simplefilter(action="ignore", category=SyntaxWarning)
        if optimize_all:
            logger.info("Finding all functions in the module '%s'…", optimize_all)
            rule()
            functions = get_all_files_and_functions(Path(optimize_all))
        elif replay_test:
            functions, trace_file_path = get_all_replay_test_functions(  # type: ignore[assignment, call-arg]
                replay_test=replay_test, test_cfg=test_cfg, project_root=project_root
            )
        elif file is not None:
            logger.info("Finding all functions in the file '%s'…", file)
            rule()
            file = Path(file) if isinstance(file, str) else file
            functions: dict[Path, list[FunctionToOptimize]] = (  # type: ignore[no-redef]
                find_all_functions_in_file(file)
            )
            if only_get_this_function is not None:
                split_function = only_get_this_function.split(".")
                if len(split_function) > 2:
                    exit_with_message(
                        "Function name should be in the format 'function_name' or 'class_name.function_name'"
                    )
                if len(split_function) == 2:
                    class_name, only_function_name = split_function
                else:
                    class_name = None
                    only_function_name = split_function[0]
                found_function = None
                for fn in functions.get(file, []):  # type: ignore[call-overload]
                    if only_function_name == fn.function_name and (
                        class_name is None or class_name == fn.top_level_parent_name
                    ):
                        found_function = fn
                if found_function is None:
                    found = closest_matching_file_function_name(
                        only_get_this_function, functions  # type: ignore[arg-type]
                    )
                    if found is not None:
                        file, found_function = found
                        exit_with_message(
                            f"Function {only_get_this_function} not found in file {file}\nor the function does not have a 'return' statement or is a property.\n"
                            f"Did you mean {found_function.qualified_name} instead?"
                        )

                    exit_with_message(
                        f"Function {only_get_this_function} not found in file {file}\nor the function does not have a 'return' statement or is a property"
                    )
                functions[file] = [found_function]  # type: ignore[index, list-item]
        else:
            logger.info("Finding all functions modified in the current git diff ...")
            rule()
            functions = get_functions_within_git_diff(uncommitted_changes=False)
        filtered_modified_functions, functions_count = filter_functions(
            functions,  # type: ignore[arg-type]
            test_cfg.tests_root,
            ignore_paths,
            project_root,
            module_root,
        )

        logger.info(
            f"Found {functions_count} function{'s' if functions_count > 1 else ''} to optimize"
        )
        return filtered_modified_functions, functions_count, trace_file_path


def get_functions_within_git_diff(
    uncommitted_changes: bool,
) -> dict[str, list[FunctionToOptimize]]:  # noqa: FBT001
    modified_lines: dict[str, list[int]] = get_git_diff(
        uncommitted_changes=uncommitted_changes
    )
    return get_functions_within_lines(modified_lines)


def closest_matching_file_function_name(
    qualified_fn_to_find: str, found_fns: dict[Path, list[FunctionToOptimize]]
) -> tuple[Path, FunctionToOptimize] | None:
    """Find the closest matching function name using Levenshtein distance.

    Args:
        qualified_fn_to_find: Function name to find in format "Class.function" or "function"
        found_fns: Dictionary of file paths to list of functions

    Returns:
        Tuple of (file_path, function) for closest match, or None if no matches found

    """
    min_distance = 4
    closest_match = None
    closest_file = None

    qualified_fn_to_find_lower = qualified_fn_to_find.lower()

    # Cache levenshtein_distance locally for improved lookup speed
    _levenshtein = levenshtein_distance

    for file_path, functions in found_fns.items():
        for function in functions:
            # Compare either full qualified name or just function name
            fn_name = function.qualified_name.lower()
            # If the absolute length difference is already >= min_distance, skip calculation
            if abs(len(qualified_fn_to_find_lower) - len(fn_name)) >= min_distance:
                continue
            dist = _levenshtein(qualified_fn_to_find_lower, fn_name)

            if dist < min_distance:
                min_distance = dist
                closest_match = function
                closest_file = file_path

    if closest_match is not None:
        return closest_file, closest_match  # type: ignore[return-value]
    return None


def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) > len(s2):
        s1, s2 = s2, s1
    len1 = len(s1)
    len2 = len(s2)
    # Use a preallocated list instead of creating a new list every iteration
    previous = list(range(len1 + 1))
    current = [0] * (len1 + 1)

    for index2 in range(len2):
        char2 = s2[index2]
        current[0] = index2 + 1
        for index1 in range(len1):
            char1 = s1[index1]
            if char1 == char2:
                current[index1 + 1] = previous[index1]
            else:
                # Fast min calculation without tuple construct
                a = previous[index1]
                b = previous[index1 + 1]
                c = current[index1]
                min_val = min(b, a)
                min_val = min(c, min_val)
                current[index1 + 1] = 1 + min_val
        # Swap references instead of copying
        previous, current = current, previous
    return previous[len1]


def get_functions_inside_a_commit(
    commit_hash: str,
) -> dict[str, list[FunctionToOptimize]]:
    modified_lines: dict[str, list[int]] = get_git_diff(only_this_commit=commit_hash)
    return get_functions_within_lines(modified_lines)


def get_functions_within_lines(
    modified_lines: dict[str, list[int]],
) -> dict[str, list[FunctionToOptimize]]:
    functions: dict[str, list[FunctionToOptimize]] = {}
    for path_str, lines_in_file in modified_lines.items():
        path = Path(path_str)
        if not path.exists():
            continue
        with path.open(encoding="utf8") as f:
            file_content = f.read()
            try:
                wrapper = cst.metadata.MetadataWrapper(cst.parse_module(file_content))
            except Exception as e:
                logger.exception(e)
                continue
            function_lines = FunctionVisitor(file_path=str(path))
            wrapper.visit(function_lines)
            functions[str(path)] = [
                function_to_optimize
                for function_to_optimize in function_lines.functions
                if (start_line := function_to_optimize.starting_line) is not None
                and (end_line := function_to_optimize.ending_line) is not None
                and any(start_line <= line <= end_line for line in lines_in_file)
            ]
    return functions


def get_all_files_and_functions(
    module_root_path: Path,
) -> dict[str, list[FunctionToOptimize]]:
    functions: dict[str, list[FunctionToOptimize]] = {}
    for file_path in module_root_path.rglob("*.py"):
        # Find all the functions in the file
        functions.update(find_all_functions_in_file(file_path).items())  # type: ignore[arg-type]
    # Randomize the order of the files to optimize to avoid optimizing the same file in the same order every time.
    # Helpful if an optimize-all run is stuck and we restart it.
    files_list = list(functions.items())
    random.shuffle(files_list)
    return dict(files_list)


def find_all_functions_in_file(file_path: Path) -> dict[Path, list[FunctionToOptimize]]:
    functions: dict[Path, list[FunctionToOptimize]] = {}
    with file_path.open(encoding="utf8") as f:
        try:
            ast_module = ast.parse(f.read())
        except Exception as e:
            if DEBUG_MODE:
                logger.exception(e)
            return functions
        function_name_visitor = FunctionWithReturnStatement(file_path)
        function_name_visitor.visit(ast_module)
        functions[file_path] = function_name_visitor.functions
    return functions


def get_all_replay_test_functions(
    replay_test: list[Path], test_cfg: TestConfig, project_root_path: Path
) -> tuple[dict[Path, list[FunctionToOptimize]], Path]:
    trace_file_path: Path | None = None
    for replay_test_file in replay_test:
        try:
            with replay_test_file.open("r", encoding="utf8") as f:
                tree = ast.parse(f.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if (
                                isinstance(target, ast.Name)
                                and target.id == "trace_file_path"
                                and isinstance(node.value, ast.Constant)
                                and isinstance(node.value.value, str)
                            ):
                                trace_file_path = Path(node.value.value)
                                break
                        if trace_file_path:
                            break
            if trace_file_path:
                break
        except Exception as e:
            logger.warning(f"Error parsing replay test file {replay_test_file}: {e}")

    if not trace_file_path:
        logger.error("Could not find trace_file_path in replay test files.")
        exit_with_message("Could not find trace_file_path in replay test files.")

    if not trace_file_path.exists():  # type: ignore[union-attr]
        logger.error(f"Trace file not found: {trace_file_path}")
        exit_with_message(
            f"Trace file not found: {trace_file_path}\n"
            "The trace file referenced in the replay test no longer exists.\n"
            "This can happen if the trace file was cleaned up after a previous optimization run.\n"
            "Please regenerate the replay test by re-running 'codeflash optimize' with your command."
        )

    function_tests, _, _ = discover_unit_tests(
        test_cfg, discover_only_these_tests=replay_test
    )
    # Get the absolute file paths for each function, excluding class name if present
    filtered_valid_functions = defaultdict(list)
    file_to_functions_map = defaultdict(list)
    # below logic can be cleaned up with a better data structure to store the function paths
    for function in function_tests:
        parts = function.split(".")
        module_path_parts = parts[:-1]  # Exclude the function or method name
        function_name = parts[-1]
        # Check if the second-to-last part is a class name
        class_name = (
            module_path_parts[-1]
            if module_path_parts
            and is_class_defined_in_file(
                module_path_parts[-1],
                Path(project_root_path, *module_path_parts[:-1]).with_suffix(".py"),
            )
            else None
        )
        if class_name:
            # If there is a class name, append it to the module path
            qualified_function_name = class_name + "." + function_name
            file_path_parts = module_path_parts[:-1]  # Exclude the class name
        else:
            qualified_function_name = function_name
            file_path_parts = module_path_parts
        file_path = Path(project_root_path, *file_path_parts).with_suffix(".py")
        if not file_path.exists():
            continue
        file_to_functions_map[file_path].append(
            (qualified_function_name, function_name, class_name)
        )
    for file_path, functions_in_file in file_to_functions_map.items():
        all_valid_functions: dict[Path, list[FunctionToOptimize]] = (
            find_all_functions_in_file(file_path=file_path)
        )
        filtered_list = []
        for func_data in functions_in_file:
            qualified_name_to_match, _, _ = func_data
            filtered_list.extend(
                [
                    valid_function
                    for valid_function in all_valid_functions[file_path]
                    if valid_function.qualified_name == qualified_name_to_match
                ]
            )
        if filtered_list:
            filtered_valid_functions[file_path] = filtered_list

    return filtered_valid_functions, trace_file_path  # type: ignore[return-value]


class TopLevelFunctionOrMethodVisitor(ast.NodeVisitor):
    def __init__(
        self,
        file_name: Path,
        function_or_method_name: str,
        class_name: str | None = None,
        line_no: int | None = None,
    ) -> None:
        self.file_name = file_name
        self.class_name = class_name
        self.function_name = function_or_method_name
        self.is_top_level = False
        self.function_has_args = None
        self.line_no = line_no
        self.is_staticmethod = False
        self.is_classmethod = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if self.class_name is None and node.name == self.function_name:
            self.is_top_level = True
            self.function_has_args = any(  # type: ignore[assignment]
                (
                    bool(node.args.args),
                    bool(node.args.kwonlyargs),
                    bool(node.args.kwarg),
                    bool(node.args.posonlyargs),
                    bool(node.args.vararg),
                )
            )

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if self.class_name is None and node.name == self.function_name:
            self.is_top_level = True
            self.function_has_args = any(  # type: ignore[assignment]
                (
                    bool(node.args.args),
                    bool(node.args.kwonlyargs),
                    bool(node.args.kwarg),
                    bool(node.args.posonlyargs),
                    bool(node.args.vararg),
                )
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # iterate over the class methods
        if node.name == self.class_name:
            for body_node in node.body:
                if (
                    isinstance(body_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and body_node.name == self.function_name
                ):
                    self.is_top_level = True
                    if any(
                        isinstance(decorator, ast.Name)
                        and decorator.id == "classmethod"
                        for decorator in body_node.decorator_list
                    ):
                        self.is_classmethod = True
                    elif any(
                        isinstance(decorator, ast.Name)
                        and decorator.id == "staticmethod"
                        for decorator in body_node.decorator_list
                    ):
                        self.is_staticmethod = True
                    return
        elif self.line_no:
            # If we have line number info, check if class has a static method with the same line number
            # This way, if we don't have the class name, we can still find the static method
            for body_node in node.body:
                if (
                    isinstance(body_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and body_node.name == self.function_name
                    and body_node.lineno in {self.line_no, self.line_no + 1}
                    and any(
                        isinstance(decorator, ast.Name)
                        and decorator.id == "staticmethod"
                        for decorator in body_node.decorator_list
                    )
                ):
                    self.is_staticmethod = True
                    self.is_top_level = True
                    self.class_name = node.name
                    return

        return


def inspect_top_level_functions_or_methods(
    file_name: Path,
    function_or_method_name: str,
    class_name: str | None = None,
    line_no: int | None = None,
) -> FunctionProperties | None:
    with file_name.open(encoding="utf8") as file:
        try:
            ast_module = ast.parse(file.read())
        except Exception:
            return None
    visitor = TopLevelFunctionOrMethodVisitor(
        file_name=file_name,
        function_or_method_name=function_or_method_name,
        class_name=class_name,
        line_no=line_no,
    )
    visitor.visit(ast_module)
    staticmethod_class_name = visitor.class_name if visitor.is_staticmethod else None
    return FunctionProperties(
        is_top_level=visitor.is_top_level,
        has_args=visitor.function_has_args,
        is_staticmethod=visitor.is_staticmethod,
        is_classmethod=visitor.is_classmethod,
        staticmethod_class_name=staticmethod_class_name,
    )


def function_has_return_statement(
    function_node: FunctionDef | AsyncFunctionDef,
) -> bool:
    # Custom DFS, return True as soon as a Return node is found
    stack = [function_node]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Return):
            return True
        stack.extend(ast.iter_child_nodes(node))  # type: ignore[arg-type]
    return False


def function_is_a_property(function_node: FunctionDef | AsyncFunctionDef) -> bool:
    for node in function_node.decorator_list:  # noqa: SIM110
        # Use isinstance rather than type(...) is ... for better performance with single inheritance trees like ast
        if isinstance(node, _ast_name) and node.id == _property_id:
            return True
    return False
