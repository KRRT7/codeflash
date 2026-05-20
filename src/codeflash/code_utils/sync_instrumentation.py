from __future__ import annotations

import ast
from pathlib import Path

from codeflash.models.domain import CodePosition, FunctionToOptimize
from codeflash.models.coverage import TestingMode

class FunctionCallNodeArguments:
    args: list[ast.expr]
    keywords: list[ast.keyword]


def get_call_arguments(call_node: ast.Call) -> FunctionCallNodeArguments:
    return FunctionCallNodeArguments(call_node.args, call_node.keywords)


def node_in_call_position(node: ast.AST, call_positions: list[CodePosition]) -> bool:
    # Profile: The most meaningful speedup here is to reduce attribute lookup and to localize call_positions if not empty.
    # Small optimizations for tight loop:
    if isinstance(node, ast.Call):
        node_lineno = getattr(node, "lineno", None)
        node_col_offset = getattr(node, "col_offset", None)
        node_end_lineno = getattr(node, "end_lineno", None)
        node_end_col_offset = getattr(node, "end_col_offset", None)
        if (
            node_lineno is not None
            and node_col_offset is not None
            and node_end_lineno is not None
        ):
            # Faster loop: reduce attribute lookups, use local variables for conditionals.
            for pos in call_positions:
                pos_line = pos.line_no
                if pos_line is not None and node_lineno <= pos_line <= node_end_lineno:
                    if pos_line == node_lineno and node_col_offset <= pos.col_no:
                        return True
                    if (
                        pos_line == node_end_lineno
                        and node_end_col_offset is not None
                        and node_end_col_offset >= pos.col_no
                    ):
                        return True
                    if node_lineno < pos_line < node_end_lineno:
                        return True
    return False


def is_argument_name(name: str, arguments_node: ast.arguments) -> bool:
    return any(
        element.arg == name
        for attribute_name in dir(arguments_node)
        if isinstance(attribute := getattr(arguments_node, attribute_name), list)
        for element in attribute
        if isinstance(element, ast.arg)
    )


class InjectPerfOnly(ast.NodeTransformer):
    def __init__(
        self,
        function: FunctionToOptimize,
        module_path: str,
        call_positions: list[CodePosition],
        mode: TestingMode = TestingMode.BEHAVIOR,
    ) -> None:
        self.mode: TestingMode = mode
        self.function_object = function
        self.class_name = None
        self.only_function_name = function.function_name
        self.module_path = module_path
        self.call_positions = call_positions
        if len(function.parents) == 1 and function.parents[0].type == "ClassDef":
            self.class_name = function.top_level_parent_name

    def find_and_update_line_node(
        self,
        test_node: ast.stmt,
        node_name: str,
        index: str,
        test_class_name: str | None = None,
    ) -> Iterable[ast.stmt] | None:
        # Major optimization: since ast.walk is *very* expensive for big trees and only checks for ast.Call,
        # it's much more efficient to visit nodes manually. We'll only descend into expressions/statements.

        # Helper for manual walk
        def iter_ast_calls(node):  # noqa: ANN202, ANN001
            # Generator to yield each ast.Call in test_node, preserves node identity
            stack = [node]
            while stack:
                n = stack.pop()
                if isinstance(n, ast.Call):
                    yield n
                # Instead of using ast.walk (which calls iter_child_nodes under the hood in Python, which copy lists and stack-frames for EVERY node),
                # do a specialized BFS with only the necessary attributes
                for _field, value in ast.iter_fields(n):
                    if isinstance(value, list):
                        for item in reversed(value):
                            if isinstance(item, ast.AST):
                                stack.append(item)  # noqa: PERF401
                    elif isinstance(value, ast.AST):
                        stack.append(value)

        # This change improves from O(N) stack-frames per child-node to a single stack, less python call overhead
        return_statement = [test_node]
        call_node = None

        # Minor optimization: Convert mode, function_name, test_class_name, qualified_name, etc to locals
        fn_obj = self.function_object
        module_path = self.module_path
        mode = self.mode
        qualified_name = fn_obj.qualified_name

        # Use locals for all 'current' values, only look up class/function/constant AST object once.
        codeflash_loop_index = ast.Name(id="codeflash_loop_index", ctx=ast.Load())
        codeflash_cur = ast.Name(id="codeflash_cur", ctx=ast.Load())
        codeflash_con = ast.Name(id="codeflash_con", ctx=ast.Load())

        for node in iter_ast_calls(test_node):
            if not node_in_call_position(node, self.call_positions):
                continue

            call_node = node
            all_args = get_call_arguments(call_node)
            # Two possible call types: Name and Attribute
            node_func = node.func

            if isinstance(node_func, ast.Name):
                function_name = node_func.id

                # Check if this is the function we want to instrument
                if function_name != fn_obj.function_name:
                    continue

                if fn_obj.is_async:
                    return [test_node]

                # Build once, reuse objects.
                inspect_name = ast.Name(id="inspect", ctx=ast.Load())
                bind_call = ast.Assign(
                    targets=[ast.Name(id="_call__bound__arguments", ctx=ast.Store())],
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Call(
                                func=ast.Attribute(
                                    value=inspect_name, attr="signature", ctx=ast.Load()
                                ),
                                args=[ast.Name(id=function_name, ctx=ast.Load())],
                                keywords=[],
                            ),
                            attr="bind",
                            ctx=ast.Load(),
                        ),
                        args=all_args.args,
                        keywords=all_args.keywords,
                    ),
                    lineno=test_node.lineno,
                    col_offset=test_node.col_offset,
                )

                apply_defaults = ast.Expr(
                    value=ast.Call(
                        func=ast.Attribute(
                            value=ast.Name(
                                id="_call__bound__arguments", ctx=ast.Load()
                            ),
                            attr="apply_defaults",
                            ctx=ast.Load(),
                        ),
                        args=[],
                        keywords=[],
                    ),
                    lineno=test_node.lineno + 1,
                    col_offset=test_node.col_offset,
                )

                node.func = ast.Name(id="codeflash_wrap", ctx=ast.Load())
                base_args = [
                    ast.Name(id=function_name, ctx=ast.Load()),
                    ast.Constant(value=module_path),
                    ast.Constant(value=test_class_name or None),
                    ast.Constant(value=node_name),
                    ast.Constant(value=qualified_name),
                    ast.Constant(value=index),
                    codeflash_loop_index,
                ]
                # Extend with BEHAVIOR extras if needed
                if mode == TestingMode.BEHAVIOR:
                    base_args += [codeflash_cur, codeflash_con]
                # Extend with call args (performance) or starred bound args (behavior)
                if mode == TestingMode.PERFORMANCE:
                    base_args += call_node.args
                else:
                    base_args.append(
                        ast.Starred(
                            value=ast.Attribute(
                                value=ast.Name(
                                    id="_call__bound__arguments", ctx=ast.Load()
                                ),
                                attr="args",
                                ctx=ast.Load(),
                            ),
                            ctx=ast.Load(),
                        )
                    )
                node.args = base_args
                # Prepare keywords
                if mode == TestingMode.BEHAVIOR:
                    node.keywords = [
                        ast.keyword(
                            value=ast.Attribute(
                                value=ast.Name(
                                    id="_call__bound__arguments", ctx=ast.Load()
                                ),
                                attr="kwargs",
                                ctx=ast.Load(),
                            )
                        )
                    ]
                else:
                    node.keywords = call_node.keywords

                return_statement = (
                    [bind_call, apply_defaults, test_node]
                    if mode == TestingMode.BEHAVIOR
                    else [test_node]
                )
                break
            if isinstance(node_func, ast.Attribute):
                function_to_test = node_func.attr
                if function_to_test == fn_obj.function_name:
                    if fn_obj.is_async:
                        return [test_node]

                    # Create the signature binding statements

                    # Unparse only once
                    function_name_expr = ast.parse(
                        ast.unparse(node_func), mode="eval"
                    ).body

                    inspect_name = ast.Name(id="inspect", ctx=ast.Load())
                    bind_call = ast.Assign(
                        targets=[
                            ast.Name(id="_call__bound__arguments", ctx=ast.Store())
                        ],
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Call(
                                    func=ast.Attribute(
                                        value=inspect_name,
                                        attr="signature",
                                        ctx=ast.Load(),
                                    ),
                                    args=[function_name_expr],
                                    keywords=[],
                                ),
                                attr="bind",
                                ctx=ast.Load(),
                            ),
                            args=all_args.args,
                            keywords=all_args.keywords,
                        ),
                        lineno=test_node.lineno,
                        col_offset=test_node.col_offset,
                    )

                    apply_defaults = ast.Expr(
                        value=ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(
                                    id="_call__bound__arguments", ctx=ast.Load()
                                ),
                                attr="apply_defaults",
                                ctx=ast.Load(),
                            ),
                            args=[],
                            keywords=[],
                        ),
                        lineno=test_node.lineno + 1,
                        col_offset=test_node.col_offset,
                    )

                    node.func = ast.Name(id="codeflash_wrap", ctx=ast.Load())
                    base_args = [
                        function_name_expr,
                        ast.Constant(value=module_path),
                        ast.Constant(value=test_class_name or None),
                        ast.Constant(value=node_name),
                        ast.Constant(value=qualified_name),
                        ast.Constant(value=index),
                        codeflash_loop_index,
                    ]
                    if mode == TestingMode.BEHAVIOR:
                        base_args += [codeflash_cur, codeflash_con]
                    if mode == TestingMode.PERFORMANCE:
                        base_args += call_node.args
                    else:
                        base_args.append(
                            ast.Starred(
                                value=ast.Attribute(
                                    value=ast.Name(
                                        id="_call__bound__arguments", ctx=ast.Load()
                                    ),
                                    attr="args",
                                    ctx=ast.Load(),
                                ),
                                ctx=ast.Load(),
                            )
                        )
                    node.args = base_args
                    if mode == TestingMode.BEHAVIOR:
                        node.keywords = [
                            ast.keyword(
                                value=ast.Attribute(
                                    value=ast.Name(
                                        id="_call__bound__arguments", ctx=ast.Load()
                                    ),
                                    attr="kwargs",
                                    ctx=ast.Load(),
                                )
                            )
                        ]
                    else:
                        node.keywords = call_node.keywords

                    # Return the signature binding statements along with the test_node
                    return_statement = (
                        [bind_call, apply_defaults, test_node]
                        if mode == TestingMode.BEHAVIOR
                        else [test_node]
                    )
                    break

        if call_node is None:
            return None
        return return_statement

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        # TODO: Ensure that this class inherits from unittest.TestCase. Don't modify non unittest.TestCase classes.
        for inner_node in ast.walk(node):
            if isinstance(inner_node, ast.FunctionDef):
                self.visit_FunctionDef(inner_node, node.name)

        return node

    def visit_FunctionDef(
        self, node: ast.FunctionDef, test_class_name: str | None = None
    ) -> ast.FunctionDef:
        if node.name.startswith("test_"):
            did_update = False
            i = len(node.body) - 1
            while i >= 0:
                line_node = node.body[i]
                # TODO: Validate if the functional call actually did not raise any exceptions

                if isinstance(line_node, (ast.With, ast.For, ast.While, ast.If)):
                    j = len(line_node.body) - 1
                    while j >= 0:
                        compound_line_node: ast.stmt = line_node.body[j]
                        internal_node: ast.AST
                        for internal_node in ast.walk(compound_line_node):
                            if isinstance(internal_node, (ast.stmt, ast.Assign)):
                                updated_node = self.find_and_update_line_node(
                                    internal_node,
                                    node.name,
                                    str(i) + "_" + str(j),
                                    test_class_name,
                                )
                                if updated_node is not None:
                                    line_node.body[j : j + 1] = updated_node
                                    did_update = True
                                    break
                        j -= 1
                else:
                    updated_node = self.find_and_update_line_node(
                        line_node, node.name, str(i), test_class_name
                    )
                    if updated_node is not None:
                        node.body[i : i + 1] = updated_node
                        did_update = True
                i -= 1
            if did_update:
                node.body = [
                    ast.Assign(
                        targets=[ast.Name(id="codeflash_loop_index", ctx=ast.Store())],
                        value=ast.Call(
                            func=ast.Name(id="int", ctx=ast.Load()),
                            args=[
                                ast.Subscript(
                                    value=ast.Attribute(
                                        value=ast.Name(id="os", ctx=ast.Load()),
                                        attr="environ",
                                        ctx=ast.Load(),
                                    ),
                                    slice=ast.Constant(value="CODEFLASH_LOOP_INDEX"),
                                    ctx=ast.Load(),
                                )
                            ],
                            keywords=[],
                        ),
                        lineno=node.lineno + 2,
                        col_offset=node.col_offset,
                    ),
                    *(
                        [
                            ast.Assign(
                                targets=[
                                    ast.Name(id="codeflash_iteration", ctx=ast.Store())
                                ],
                                value=ast.Subscript(
                                    value=ast.Attribute(
                                        value=ast.Name(id="os", ctx=ast.Load()),
                                        attr="environ",
                                        ctx=ast.Load(),
                                    ),
                                    slice=ast.Constant(
                                        value="CODEFLASH_TEST_ITERATION"
                                    ),
                                    ctx=ast.Load(),
                                ),
                                lineno=node.lineno + 1,
                                col_offset=node.col_offset,
                            ),
                            ast.Assign(
                                targets=[ast.Name(id="codeflash_con", ctx=ast.Store())],
                                value=ast.Call(
                                    func=ast.Attribute(
                                        value=ast.Name(id="sqlite3", ctx=ast.Load()),
                                        attr="connect",
                                        ctx=ast.Load(),
                                    ),
                                    args=[
                                        ast.JoinedStr(
                                            values=[
                                                ast.Constant(
                                                    value=f"{get_run_tmp_file(Path('test_return_values_')).as_posix()}"
                                                ),
                                                ast.FormattedValue(
                                                    value=ast.Name(
                                                        id="codeflash_iteration",
                                                        ctx=ast.Load(),
                                                    ),
                                                    conversion=-1,
                                                ),
                                                ast.Constant(value=".sqlite"),
                                            ]
                                        )
                                    ],
                                    keywords=[],
                                ),
                                lineno=node.lineno + 3,
                                col_offset=node.col_offset,
                            ),
                            ast.Assign(
                                targets=[ast.Name(id="codeflash_cur", ctx=ast.Store())],
                                value=ast.Call(
                                    func=ast.Attribute(
                                        value=ast.Name(
                                            id="codeflash_con", ctx=ast.Load()
                                        ),
                                        attr="cursor",
                                        ctx=ast.Load(),
                                    ),
                                    args=[],
                                    keywords=[],
                                ),
                                lineno=node.lineno + 4,
                                col_offset=node.col_offset,
                            ),
                            ast.Expr(
                                value=ast.Call(
                                    func=ast.Attribute(
                                        value=ast.Name(
                                            id="codeflash_cur", ctx=ast.Load()
                                        ),
                                        attr="execute",
                                        ctx=ast.Load(),
                                    ),
                                    args=[
                                        ast.Constant(value=TEST_RESULTS_TABLE_SCHEMA)
                                    ],
                                    keywords=[],
                                ),
                                lineno=node.lineno + 5,
                                col_offset=node.col_offset,
                            ),
                        ]
                        if self.mode == TestingMode.BEHAVIOR
                        else []
                    ),
                    *node.body,
                    *(
                        [
                            ast.Expr(
                                value=ast.Call(
                                    func=ast.Attribute(
                                        value=ast.Name(
                                            id="codeflash_con", ctx=ast.Load()
                                        ),
                                        attr="close",
                                        ctx=ast.Load(),
                                    ),
                                    args=[],
                                    keywords=[],
                                )
                            )
                        ]
                        if self.mode == TestingMode.BEHAVIOR
                        else []
                    ),
                ]
        return node


