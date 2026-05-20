from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


from codeflash.cli_cmds.logging_config import logger
from codeflash.models.domain import CodePosition
from codeflash.code_utils.config_consts import MAX_CONTEXT_LEN_REVIEW
from typing import Union
import jedi
import time
from importlib.util import find_spec

class FunctionCallLocation:
    """Represents a location where the target function is called."""

    calling_function: str
    line: int
    column: int


@dataclass
class FunctionDefinitionInfo:
    """Contains information about a function definition."""

    name: str
    node: ast.FunctionDef
    source_code: str
    start_line: int
    end_line: int
    is_method: bool
    class_name: str | None = None


class FunctionCallFinder(ast.NodeVisitor):
    """AST visitor that finds all function definitions that call a specific qualified function.

    Args:
        target_function_name: The qualified name of the function to find (e.g., "module.function" or "function")
        target_filepath: The filepath where the target function is defined

    """

    def __init__(
        self, target_function_name: str, target_filepath: str, source_lines: list[str]
    ) -> None:
        self.target_function_name = target_function_name
        self.target_filepath = target_filepath
        self.source_lines = source_lines  # Store original source lines for extraction

        # Parse the target function name into parts
        self.target_parts = target_function_name.split(".")
        self.target_base_name = self.target_parts[-1]

        # Track current context
        self.current_function_stack: list[tuple[str, ast.FunctionDef]] = []
        self.current_class_stack: list[str] = []

        # Track imports to resolve qualified names
        self.imports: dict[str, str] = {}  # Maps imported names to their full paths

        # Results
        self.function_calls: list[FunctionCallLocation] = []
        self.calling_functions: set[str] = set()
        self.function_definitions: dict[str, FunctionDefinitionInfo] = {}

        # Track if we found calls in the current function
        self.found_call_in_current_function = False
        self.functions_with_nested_calls: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        """Track regular imports."""
        for alias in node.names:
            if alias.asname:
                # import module as alias
                self.imports[alias.asname] = alias.name
            else:
                # import module
                self.imports[alias.name.split(".")[-1]] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Track from imports."""
        if node.module:
            for alias in node.names:
                if alias.name == "*":
                    # from module import *
                    self.imports["*"] = node.module
                elif alias.asname:
                    # from module import name as alias
                    self.imports[alias.asname] = f"{node.module}.{alias.name}"
                else:
                    # from module import name
                    self.imports[alias.name] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track when entering a class definition."""
        self.current_class_stack.append(node.name)
        self.generic_visit(node)
        self.current_class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Track when entering a function definition."""
        self._visit_function_def(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Track when entering an async function definition."""
        self._visit_function_def(node)

    def _visit_function_def(self, node: ast.FunctionDef) -> None:
        """Track when entering a function definition."""
        func_name = node.name

        # Build the full qualified name including class if applicable
        full_name = (
            f"{'.'.join(self.current_class_stack)}.{func_name}"
            if self.current_class_stack
            else func_name
        )

        self.current_function_stack.append((full_name, node))
        self.found_call_in_current_function = False

        # Visit the function body
        self.generic_visit(node)

        # Process the function after visiting its body
        if (
            self.found_call_in_current_function
            and full_name not in self.function_definitions
        ):
            # Extract function source code
            source_code = self._extract_source_code(node)

            self.function_definitions[full_name] = FunctionDefinitionInfo(
                name=full_name,
                node=node,
                source_code=source_code,
                start_line=node.lineno,
                end_line=node.end_lineno
                if hasattr(node, "end_lineno")
                else node.lineno,
                is_method=bool(self.current_class_stack),
                class_name=self.current_class_stack[-1]
                if self.current_class_stack
                else None,
            )

        # Handle nested functions - mark parent as containing nested calls
        if self.found_call_in_current_function and len(self.current_function_stack) > 1:
            parent_name = self.current_function_stack[-2][0]
            self.functions_with_nested_calls.add(parent_name)

            # Also store the parent function if not already stored
            if parent_name not in self.function_definitions:
                parent_node = self.current_function_stack[-2][1]
                parent_source = self._extract_source_code(parent_node)

                # Check if parent is a method (excluding current level)
                parent_class_context = (
                    self.current_class_stack
                    if len(self.current_function_stack) == 2
                    else []
                )

                self.function_definitions[parent_name] = FunctionDefinitionInfo(
                    name=parent_name,
                    node=parent_node,
                    source_code=parent_source,
                    start_line=parent_node.lineno,
                    end_line=parent_node.end_lineno
                    if hasattr(parent_node, "end_lineno")
                    else parent_node.lineno,
                    is_method=bool(parent_class_context),
                    class_name=parent_class_context[-1]
                    if parent_class_context
                    else None,
                )

        self.current_function_stack.pop()

        # Reset flag for parent function
        if self.current_function_stack:
            parent_name = self.current_function_stack[-1][0]
            self.found_call_in_current_function = parent_name in self.calling_functions

    def visit_Call(self, node: ast.Call) -> None:
        """Check if this call matches our target function."""
        if not self.current_function_stack:
            # Not inside a function, skip
            self.generic_visit(node)
            return

        if self._is_target_function_call(node):
            current_func_name = self.current_function_stack[-1][0]

            call_location = FunctionCallLocation(
                calling_function=current_func_name,
                line=node.lineno,
                column=node.col_offset,
            )

            self.function_calls.append(call_location)
            self.calling_functions.add(current_func_name)
            self.found_call_in_current_function = True

        self.generic_visit(node)

    def _is_target_function_call(self, node: ast.Call) -> bool:
        """Determine if this call node is calling our target function."""
        call_name = self._get_call_name(node.func)
        if not call_name:
            return False

        # Check if it matches directly
        if call_name == self.target_function_name:
            return True

        # Check if it's just the base name matching
        if call_name == self.target_base_name:
            # Could be imported with a different name, check imports
            if call_name in self.imports:
                imported_path = self.imports[call_name]
                if imported_path == self.target_function_name or imported_path.endswith(
                    f".{self.target_function_name}"
                ):
                    return True
            # Could also be a direct call if we're in the same file
            return True

        # Check for qualified calls with imports
        call_parts = call_name.split(".")
        if call_parts[0] in self.imports:
            # Resolve the full path using imports
            base_import = self.imports[call_parts[0]]
            full_path = (
                f"{base_import}.{'.'.join(call_parts[1:])}"
                if len(call_parts) > 1
                else base_import
            )

            if full_path == self.target_function_name or full_path.endswith(
                f".{self.target_function_name}"
            ):
                return True

        return False

    def _get_call_name(self, func_node) -> str | None:  # noqa: ANN001
        """Extract the name being called from a function node."""
        # Fast path short-circuit for ast.Name nodes
        if isinstance(func_node, ast.Name):
            return func_node.id

        # Fast attribute chain extraction (speed: append, loop, join, NO reversed)
        if isinstance(func_node, ast.Attribute):
            parts = []
            current = func_node
            # Unwind attribute chain as tight as possible (checked at each loop iteration)
            while True:
                parts.append(current.attr)
                val = current.value
                if isinstance(val, ast.Attribute):
                    current = val
                    continue
                if isinstance(val, ast.Name):
                    parts.append(val.id)
                    # Join in-place backwards via slice instead of reversed for slight speedup
                    return ".".join(parts[::-1])
                break
        return None

    def _extract_source_code(self, node: ast.FunctionDef) -> str:
        """Extract source code for a function node using original source lines."""
        if not self.source_lines or not hasattr(node, "lineno"):
            # Fallback to ast.unparse if available (Python 3.9+)
            try:
                return ast.unparse(node)
            except AttributeError:
                return f"# Source code extraction not available for {node.name}"

        # Get the lines for this function
        start_line = node.lineno - 1  # Convert to 0-based index
        end_line = (
            node.end_lineno if hasattr(node, "end_lineno") else len(self.source_lines)
        )

        # Extract the function lines
        func_lines = self.source_lines[start_line:end_line]

        # Find the minimum indentation (excluding empty lines)
        min_indent = float("inf")
        for line in func_lines:
            if line.strip():  # Skip empty lines
                indent = len(line) - len(line.lstrip())
                min_indent = min(min_indent, indent)

        # If this is a method (inside a class), preserve one level of indentation
        if self.current_class_stack:
            # Keep 4 spaces of indentation for methods
            dedent_amount = max(0, min_indent - 4)
            result_lines = []
            for line in func_lines:
                if line.strip():  # Only dedent non-empty lines
                    result_lines.append(
                        line[dedent_amount:] if len(line) > dedent_amount else line
                    )
                else:
                    result_lines.append(line)
        else:
            # For top-level functions, remove all leading indentation
            result_lines = []
            for line in func_lines:
                if line.strip():  # Only dedent non-empty lines
                    result_lines.append(
                        line[min_indent:] if len(line) > min_indent else line
                    )
                else:
                    result_lines.append(line)

        return "".join(result_lines).rstrip()

    def get_results(self) -> dict[str, str]:
        """Get the results of the analysis.

        Returns:
            A dictionary mapping qualified function names to their source code definitions.

        """
        return {
            info.name: info.source_code for info in self.function_definitions.values()
        }


def find_function_calls(
    source_code: str, target_function_name: str, target_filepath: str
) -> dict[str, str]:
    """Find all function definitions that call a specific target function.

    Args:
        source_code: The Python source code to analyze
        target_function_name: The qualified name of the function to find (e.g., "module.function")
        target_filepath: The filepath where the target function is defined

    Returns:
        A dictionary mapping qualified function names to their source code definitions.
        Example: {"function_a": "def function_a():    ...", "MyClass.method_one": "def method_one(self):    ..."}

    """
    # Parse the source code
    tree = ast.parse(source_code)

    # Split source into lines for source extraction
    source_lines = source_code.splitlines(keepends=True)

    # Create and run the visitor
    visitor = FunctionCallFinder(target_function_name, target_filepath, source_lines)
    visitor.visit(tree)

    return visitor.get_results()


def find_occurances(
    qualified_name: str,
    file_path: str,
    fn_matches: list[Path],
    project_root: Path,
    tests_root: Path,
) -> list[str]:  # max chars for context
    context_len = 0
    fn_call_context = ""
    for cur_file in fn_matches:
        if context_len > MAX_CONTEXT_LEN_REVIEW:
            break
        cur_file_path = Path(cur_file)
        # exclude references in tests
        try:
            if cur_file_path.relative_to(tests_root):
                continue
        except ValueError:
            pass
        with cur_file_path.open(encoding="utf8") as f:
            file_content = f.read()
        results = find_function_calls(
            file_content, target_function_name=qualified_name, target_filepath=file_path
        )
        if results:
            try:
                path_relative_to_project_root = cur_file_path.relative_to(project_root)
            except Exception as e:
                # shouldn't happen but ensuring we don't crash
                logger.debug(f"investigate {e}")
                continue
            fn_call_context += f"```python:{path_relative_to_project_root}\n"
            for fn_definition in (
                results.values()
            ):  # multiple functions in the file might be calling the desired function
                fn_call_context += f"{fn_definition}\n"
                context_len += len(fn_definition)
            fn_call_context += "```\n"
    return fn_call_context


def find_specific_function_in_file(
    source_code: str,
    filepath: Union[str, Path],
    target_function: str,
    target_class: str | None,
) -> tuple[int, int] | None:
    """Find a specific function definition in a Python file and return its location.

    Stops searching once the target is found (optimized for performance).

    Args:
        source_code: Source code string
        filepath: Path to the Python file
        target_function: Function Name of the function to find
        target_class: Class name of the function to find

    Returns:
        Tuple of (line_number, column_offset) if found, None otherwise

    """
    script = jedi.Script(code=source_code, path=filepath)
    names = script.get_names(all_scopes=True, definitions=True)
    for name in names:
        if name.type == "function" and name.name == target_function:
            # If class name specified, check parent
            if target_class:
                parent = name.parent()
                if parent and parent.name == target_class and parent.type == "class":
                    return CodePosition(line_no=name.line, col_no=name.column)
            else:
                # Top-level function match
                return CodePosition(line_no=name.line, col_no=name.column)

    return None  # Function not found


def get_fn_references_jedi(
    source_code: str,
    file_path: Path,
    project_root: Path,
    target_function: str,
    target_class: str | None,
) -> list[Path]:
    start_time = time.perf_counter()
    function_position: CodePosition = find_specific_function_in_file(
        source_code, file_path, target_function, target_class
    )
    try:
        script = jedi.Script(
            code=source_code, path=file_path, project=jedi.Project(path=project_root)
        )
        # Get references to the function
        references = script.get_references(
            line=function_position.line_no, column=function_position.col_no
        )
        # Collect unique file paths where references are found
        end_time = time.perf_counter()
        logger.debug(
            f"Jedi for function references ran in {end_time - start_time:.2f} seconds"
        )
        reference_files = set()
        for ref in references:
            if ref.module_path:
                # Convert to string and normalize path
                ref_path = str(ref.module_path)
                # Skip the definition itself
                if not (
                    ref_path == file_path and ref.line == function_position.line_no
                ):
                    reference_files.add(ref_path)
        return sorted(reference_files)
    except Exception as e:
        print(f"Error during Jedi analysis: {e}")
        return []


has_numba = find_spec("numba") is not None

NUMERICAL_MODULES = frozenset(
    {"numpy", "torch", "numba", "jax", "tensorflow", "math", "scipy"}
)
# Modules that require numba to be installed for optimization
NUMBA_REQUIRED_MODULES = frozenset({"numpy", "math", "scipy"})


class NumericalUsageChecker(ast.NodeVisitor):
    """AST visitor that checks if a function uses numerical computing libraries."""

    def __init__(self, numerical_names: set[str]) -> None:
        self.numerical_names = numerical_names
        self.found_numerical = False

    def visit_Call(self, node: ast.Call) -> None:
        """Check function calls for numerical library usage."""
        if self.found_numerical:
            return
        call_name = self._get_root_name(node.func)
        if call_name and call_name in self.numerical_names:
            self.found_numerical = True
            return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check attribute access for numerical library usage."""
        if self.found_numerical:
            return
        root_name = self._get_root_name(node)
        if root_name and root_name in self.numerical_names:
            self.found_numerical = True
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Check name references for numerical library usage."""
        if self.found_numerical:
            return
        if node.id in self.numerical_names:
            self.found_numerical = True

    def _get_root_name(self, node: ast.expr) -> str | None:
        """Get the root name from an expression (e.g., 'np' from 'np.array')."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return self._get_root_name(node.value)
        return None


def _collect_numerical_imports(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Collect names that reference numerical computing libraries from imports.

    Returns:
        A tuple of (numerical_names, modules_used) where:
        - numerical_names: set of names/aliases that reference numerical libraries
        - modules_used: set of actual module names (e.g., "numpy", "math") being imported

    """
    numerical_names: set[str] = set()
    modules_used: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # import numpy or import numpy as np
                module_root = alias.name.split(".")[0]
                if module_root in NUMERICAL_MODULES:
                    # Use the alias if present, otherwise the module name
                    name = alias.asname if alias.asname else alias.name.split(".")[0]
                    numerical_names.add(name)
                    modules_used.add(module_root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_root = node.module.split(".")[0]
            if module_root in NUMERICAL_MODULES:
                # from numpy import array, zeros as z
                for alias in node.names:
                    if alias.name == "*":
                        # Can't track star imports, but mark the module as numerical
                        numerical_names.add(module_root)
                    else:
                        name = alias.asname if alias.asname else alias.name
                        numerical_names.add(name)
                modules_used.add(module_root)

    return numerical_names, modules_used


def _find_function_node(
    tree: ast.Module, name_parts: list[str]
) -> ast.FunctionDef | None:
    """Find a function node in the AST given its qualified name parts.

    Note: This function only finds regular (sync) functions, not async functions.

    Args:
        tree: The parsed AST module
        name_parts: List of name parts, e.g., ["ClassName", "method_name"] or ["function_name"]

    Returns:
        The function node if found, None otherwise

    """
    if not name_parts:
        return None

    if len(name_parts) == 1:
        # Top-level function
        func_name = name_parts[0]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return node
        return None

    if len(name_parts) == 2:
        # Class method: ClassName.method_name
        class_name, method_name = name_parts
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for class_node in node.body:
                    if (
                        isinstance(class_node, ast.FunctionDef)
                        and class_node.name == method_name
                    ):
                        return class_node
        return None

    return None


def is_numerical_code(code_string: str, function_name: str) -> bool:
    """Check if a function uses numerical computing libraries.

    Detects usage of numpy, torch, numba, jax, tensorflow, scipy, and math libraries
    within the specified function.

    Note: For math, numpy, and scipy usage, this function returns True only if numba
    is installed in the environment, as numba is required to optimize such code.

    Args:
        code_string: The entire file's content as a string
        function_name: The name of the function to check. Can be a simple name like "foo"
                      or a qualified name like "ClassName.method_name" for methods,
                      staticmethods, or classmethods.

    Returns:
        True if the function uses any numerical computing library functions, False otherwise.
        Returns False for math/numpy/scipy usage if numba is not installed.

    Examples:
        >>> code = '''
        ... import numpy as np
        ... def process_data(x):
        ...     return np.sum(x)
        ... '''
        >>> is_numerical_code(code, "process_data")  # Returns True only if numba is installed
        True

        >>> code = '''
        ... def simple_func(x):
        ...     return x + 1
        ... '''
        >>> is_numerical_code(code, "simple_func")
        False

    """
    try:
        tree = ast.parse(code_string)
    except SyntaxError:
        return False

    # Split the function name to handle class methods
    name_parts = function_name.split(".")

    # Find the target function node
    target_function = _find_function_node(tree, name_parts)
    if target_function is None:
        return False

    # Collect names that reference numerical modules from imports
    numerical_names, modules_used = _collect_numerical_imports(tree)

    # Check if the function body uses any numerical library
    checker = NumericalUsageChecker(numerical_names)
    checker.visit(target_function)

    if not checker.found_numerical:
        return False

    # If numba is not installed and all modules used require numba for optimization,
    # return False since we can't optimize this code
    if not has_numba and modules_used.issubset(NUMBA_REQUIRED_MODULES):  # noqa : SIM103
        return False

    return True


