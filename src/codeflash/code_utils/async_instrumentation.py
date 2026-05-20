from __future__ import annotations

import ast
from pathlib import Path

import libcst as cst

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.formatter import sort_imports
from codeflash.discovery.functions_to_optimize import FunctionToOptimize
from codeflash.models.domain import CodePosition
from codeflash.models.coverage import TestingMode

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
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(
        self, node: ast.AsyncFunctionDef
    ) -> ast.AsyncFunctionDef:
        if not node.name.startswith("test_"):
            return node

        return self._process_test_function(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        # Only process test functions
        if not node.name.startswith("test_"):
            return node

        return self._process_test_function(node)

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

            new_body.append(transformed_stmt)

        node.body = new_body
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
                    stack.append(child)
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


class AsyncDecoratorAdder(cst.CSTTransformer):
    """Transformer that adds async decorator to async function definitions."""

    def __init__(
        self, function: FunctionToOptimize, mode: TestingMode = TestingMode.BEHAVIOR
    ) -> None:
        """Initialize the transformer.

        Args:
        ----
            function: The FunctionToOptimize object representing the target async function.
            mode: The testing mode to determine which decorator to apply.

        """
        super().__init__()
        self.function = function
        self.mode = mode
        self.qualified_name_parts = function.qualified_name.split(".")
        self.context_stack = []
        self.added_decorator = False

        # Choose decorator based on mode
        self.decorator_name = (
            "codeflash_behavior_async"
            if mode == TestingMode.BEHAVIOR
            else "codeflash_performance_async"
        )

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        # Track when we enter a class
        self.context_stack.append(node.name.value)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:  # noqa: ARG002
        # Pop the context when we leave a class
        self.context_stack.pop()
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        # Track when we enter a function
        self.context_stack.append(node.name.value)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        # Check if this is an async function and matches our target
        if (
            original_node.asynchronous is not None
            and self.context_stack == self.qualified_name_parts
        ):
            # Check if the decorator is already present
            has_decorator = any(
                self._is_target_decorator(decorator.decorator)
                for decorator in original_node.decorators
            )

            # Only add the decorator if it's not already there
            if not has_decorator:
                new_decorator = cst.Decorator(
                    decorator=cst.Name(value=self.decorator_name)
                )

                # Add our new decorator to the existing decorators
                updated_decorators = [new_decorator, *list(updated_node.decorators)]
                updated_node = updated_node.with_changes(
                    decorators=tuple(updated_decorators)
                )
                self.added_decorator = True

        # Pop the context when we leave a function
        self.context_stack.pop()
        return updated_node

    def _is_target_decorator(
        self, decorator_node: cst.Name | cst.Attribute | cst.Call
    ) -> bool:
        """Check if a decorator matches our target decorator name."""
        if isinstance(decorator_node, cst.Name):
            return decorator_node.value in {
                "codeflash_trace_async",
                "codeflash_behavior_async",
                "codeflash_performance_async",
            }
        if isinstance(decorator_node, cst.Call) and isinstance(
            decorator_node.func, cst.Name
        ):
            return decorator_node.func.value in {
                "codeflash_trace_async",
                "codeflash_behavior_async",
                "codeflash_performance_async",
            }
        return False


class AsyncDecoratorImportAdder(cst.CSTTransformer):
    """Transformer that adds the import for async decorators."""

    def __init__(self, mode: TestingMode = TestingMode.BEHAVIOR) -> None:
        self.mode = mode
        self.has_import = False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        # Check if the async decorator import is already present
        if (
            isinstance(node.module, cst.Attribute)
            and isinstance(node.module.value, cst.Attribute)
            and isinstance(node.module.value.value, cst.Name)
            and node.module.value.value.value == "codeflash"
            and node.module.value.attr.value == "code_utils"
            and node.module.attr.value == "codeflash_wrap_decorator"
            and not isinstance(node.names, cst.ImportStar)
        ):
            decorator_name = (
                "codeflash_behavior_async"
                if self.mode == TestingMode.BEHAVIOR
                else "codeflash_performance_async"
            )
            for import_alias in node.names:
                if import_alias.name.value == decorator_name:
                    self.has_import = True

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:  # noqa: ARG002
        # If the import is already there, don't add it again
        if self.has_import:
            return updated_node

        # Choose import based on mode
        decorator_name = (
            "codeflash_behavior_async"
            if self.mode == TestingMode.BEHAVIOR
            else "codeflash_performance_async"
        )

        # Parse the import statement into a CST node
        import_node = cst.parse_statement(
            f"from codeflash.code_utils.codeflash_wrap_decorator import {decorator_name}"
        )

        # Add the import to the module's body
        return updated_node.with_changes(body=[import_node, *list(updated_node.body)])


def add_async_decorator_to_function(
    source_path: Path,
    function: FunctionToOptimize,
    mode: TestingMode = TestingMode.BEHAVIOR,
) -> bool:
    """Add async decorator to an async function definition and write back to file.

    Args:
    ----
        source_path: Path to the source file to modify in-place.
        function: The FunctionToOptimize object representing the target async function.
        mode: The testing mode to determine which decorator to apply.

    Returns:
    -------
        Boolean indicating whether the decorator was successfully added.

    """
    if not function.is_async:
        return False

    try:
        # Read source code
        with source_path.open(encoding="utf8") as f:
            source_code = f.read()

        module = cst.parse_module(source_code)

        # Add the decorator to the function
        decorator_transformer = AsyncDecoratorAdder(function, mode)
        module = module.visit(decorator_transformer)

        # Add the import if decorator was added
        if decorator_transformer.added_decorator:
            import_transformer = AsyncDecoratorImportAdder(mode)
            module = module.visit(import_transformer)

        modified_code = sort_imports(code=module.code, float_to_top=True)
    except Exception as e:
        logger.exception(
            f"Error adding async decorator to function {function.qualified_name}: {e}"
        )
        return False
    else:
        if decorator_transformer.added_decorator:
            with source_path.open("w", encoding="utf8") as f:
                f.write(modified_code)
            logger.debug(f"Applied async {mode.value} instrumentation to {source_path}")
            return True
        return False


def create_instrumented_source_module_path(source_path: Path, temp_dir: Path) -> Path:
    instrumented_filename = f"instrumented_{source_path.name}"
    return temp_dir / instrumented_filename
