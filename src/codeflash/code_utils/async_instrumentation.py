from __future__ import annotations

import ast
from pathlib import Path

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.formatter import sort_imports
from codeflash.code_utils.sync_instrumentation import node_in_call_position
from codeflash.discovery.functions_to_optimize import FunctionToOptimize
from codeflash.models.domain import CodePosition, FunctionParent
from codeflash.models.coverage import TestingMode
from codeflash.code_utils.path_utils import module_name_from_file_path


class AsyncCallInstrumenter(ast.NodeTransformer):
    def __init__(
        self,
        function: FunctionToOptimize,
        module_path: str,
        call_positions: list[CodePosition],
        mode: TestingMode = TestingMode.BEHAVIOR,
    ) -> None:
        self.mode = mode
        self.function_object = function
        self.class_name = None
        self.only_function_name = function.function_name
        self.module_path = module_path
        self.call_positions = call_positions
        self.did_instrument = False
        # Track function call count per test function
        self.async_call_counter: dict[str, int] = {}
        if len(function.parents) == 1 and function.parents[0].type == "ClassDef":
            self.class_name = function.top_level_parent_name

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        return self.generic_visit(node)  # type: ignore[return-value]

    def visit_AsyncFunctionDef(
        self, node: ast.AsyncFunctionDef
    ) -> ast.AsyncFunctionDef:
        if not node.name.startswith("test_"):
            return node

        return self._process_test_function(node)  # type: ignore[return-value]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        # Only process test functions
        if not node.name.startswith("test_"):
            return node

        return self._process_test_function(node)  # type: ignore[return-value]

    def _process_test_function(
        self, node: ast.AsyncFunctionDef | ast.FunctionDef
    ) -> ast.AsyncFunctionDef | ast.FunctionDef:
        # Initialize counter for this test function
        if node.name not in self.async_call_counter:
            self.async_call_counter[node.name] = 0

        new_body = []

        # Optimize ast.walk calls inside _instrument_statement, by scanning only relevant nodes
        for _i, stmt in enumerate(node.body):
            transformed_stmt, added_env_assignment = (
                self._optimized_instrument_statement(stmt)
            )

            if added_env_assignment:
                current_call_index = self.async_call_counter[node.name]
                self.async_call_counter[node.name] += 1

                env_assignment = ast.Assign(
                    targets=[
                        ast.Subscript(
                            value=ast.Attribute(
                                value=ast.Name(id="os", ctx=ast.Load()),
                                attr="environ",
                                ctx=ast.Load(),
                            ),
                            slice=ast.Constant(value="CODEFLASH_CURRENT_LINE_ID"),
                            ctx=ast.Store(),
                        )
                    ],
                    value=ast.Constant(value=f"{current_call_index}"),
                    lineno=stmt.lineno if hasattr(stmt, "lineno") else 1,
                )
                new_body.append(env_assignment)
                self.did_instrument = True

            new_body.append(transformed_stmt)  # type: ignore[arg-type]

        node.body = new_body  # type: ignore[assignment]
        return node

    def _instrument_statement(
        self, stmt: ast.stmt, _node_name: str
    ) -> tuple[ast.stmt, bool]:
        for node in ast.walk(stmt):
            if (
                isinstance(node, ast.Await)
                and isinstance(node.value, ast.Call)
                and self._is_target_call(node.value)
                and self._call_in_positions(node.value)
            ):
                # Check if this call is in one of our target positions
                return (
                    stmt,
                    True,
                )  # Return original statement but signal we added env var

        return stmt, False

    def _is_target_call(self, call_node: ast.Call) -> bool:
        """Check if this call node is calling our target async function."""
        if isinstance(call_node.func, ast.Name):
            return call_node.func.id == self.function_object.function_name
        if isinstance(call_node.func, ast.Attribute):
            return call_node.func.attr == self.function_object.function_name
        return False

    def _call_in_positions(self, call_node: ast.Call) -> bool:
        if not hasattr(call_node, "lineno") or not hasattr(call_node, "col_offset"):
            return False

        return node_in_call_position(call_node, self.call_positions)

    # Optimized version: only walk child nodes for Await
    def _optimized_instrument_statement(self, stmt: ast.stmt) -> tuple[ast.stmt, bool]:
        # Stack-based DFS, manual for relevant Await nodes
        stack = [stmt]
        while stack:
            node = stack.pop()
            # Favor direct ast.Await detection
            if isinstance(node, ast.Await):
                val = node.value
                if (
                    isinstance(val, ast.Call)
                    and self._is_target_call(val)
                    and self._call_in_positions(val)
                ):
                    return stmt, True
            # Use _fields instead of ast.walk for less allocations
            for fname in getattr(node, "_fields", ()):
                child = getattr(node, fname, None)
                if isinstance(child, list):
                    stack.extend(child)
                elif isinstance(child, ast.AST):
                    stack.append(child)  # type: ignore[arg-type]
        return stmt, False


class FunctionImportedAsVisitor(ast.NodeVisitor):
    """Checks if a function has been imported as an alias. We only care about the alias then.

    from numpy import array as np_array
    np_array is what we want
    """

    def __init__(self, function: FunctionToOptimize) -> None:
        assert len(function.parents) <= 1, (
            "Only support functions with one or less parent"
        )
        self.imported_as = function
        self.function = function
        if function.parents:
            self.to_match = function.parents[0].name
        else:
            self.to_match = function.function_name

    # TODO: Validate if the function imported is actually from the right module
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if (
                alias.name == self.to_match
                and hasattr(alias, "asname")
                and alias.asname is not None
            ):
                if self.function.parents:
                    self.imported_as = FunctionToOptimize(
                        function_name=self.function.function_name,
                        parents=[FunctionParent(alias.asname, "ClassDef")],
                        file_path=self.function.file_path,
                        starting_line=self.function.starting_line,
                        ending_line=self.function.ending_line,
                        is_async=self.function.is_async,
                    )
                else:
                    self.imported_as = FunctionToOptimize(
                        function_name=alias.asname,
                        parents=[],
                        file_path=self.function.file_path,
                        starting_line=self.function.starting_line,
                        ending_line=self.function.ending_line,
                        is_async=self.function.is_async,
                    )


def inject_async_profiling_into_existing_test(
    test_path: Path,
    call_positions: list[CodePosition],
    function_to_optimize: FunctionToOptimize,
    tests_project_root: Path,
    mode: TestingMode = TestingMode.BEHAVIOR,
) -> tuple[bool, str | None]:
    """Inject profiling for async function calls by setting environment variables before each call."""
    with test_path.open(encoding="utf8") as f:
        test_code = f.read()

    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        logger.exception(f"Syntax error in code in file - {test_path}")
        return False, None
    # TODO: Pass the full name of function here, otherwise we can run into namespace clashes
    test_module_path = module_name_from_file_path(test_path, tests_project_root)
    import_visitor = FunctionImportedAsVisitor(function_to_optimize)
    import_visitor.visit(tree)
    func = import_visitor.imported_as

    async_instrumenter = AsyncCallInstrumenter(
        func, test_module_path, call_positions, mode=mode
    )
    tree = async_instrumenter.visit(tree)

    if not async_instrumenter.did_instrument:
        return False, None

    # Add necessary imports
    new_imports = [ast.Import(names=[ast.alias(name="os")])]

    tree.body = [*new_imports, *tree.body]
    return True, sort_imports(ast.unparse(tree), float_to_top=True)


def create_instrumented_source_module_path(source_path: Path, temp_dir: Path) -> Path:
    instrumented_filename = f"instrumented_{source_path.name}"
    return temp_dir / instrumented_filename
