from __future__ import annotations

import libcst as cst


class GlobalStatementCollector(cst.CSTVisitor):
    """Visitor that collects all global statements (excluding imports and functions/classes)."""

    def __init__(self) -> None:
        super().__init__()
        self.global_statements = []
        self.in_function_or_class = False

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:  # noqa: ARG002
        # Don't visit inside classes
        self.in_function_or_class = True
        return False

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:  # noqa: ARG002
        self.in_function_or_class = False

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:  # noqa: ARG002
        # Don't visit inside functions
        self.in_function_or_class = True
        return False

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:  # noqa: ARG002
        self.in_function_or_class = False

    def visit_SimpleStatementLine(self, node: cst.SimpleStatementLine) -> None:
        if not self.in_function_or_class:
            for statement in node.body:
                # Skip imports
                if not isinstance(statement, (cst.Import, cst.ImportFrom, cst.Assign)):
                    self.global_statements.append(node)
                    break


class LastImportFinder(cst.CSTVisitor):
    """Finds the position of the last import statement in the module."""

    def __init__(self) -> None:
        super().__init__()
        self.last_import_line = 0
        self.current_line = 0

    def visit_SimpleStatementLine(self, node: cst.SimpleStatementLine) -> None:
        self.current_line += 1
        for statement in node.body:
            if isinstance(statement, (cst.Import, cst.ImportFrom)):
                self.last_import_line = self.current_line


class DottedImportCollector(cst.CSTVisitor):
    """Collects all top-level imports from a Python module in normalized dotted format, including top-level conditional imports like `if TYPE_CHECKING:`.

    Examples
    --------
        import os                                                                  ==> "os"
        import dbt.adapters.factory                                                ==> "dbt.adapters.factory"
        from pathlib import Path                                                   ==> "pathlib.Path"
        from recce.adapter.base import BaseAdapter                                 ==> "recce.adapter.base.BaseAdapter"
        from typing import Any, List                                     ==> "typing.Any", "typing.List", "typing."
        from recce.util.lineage import ( build_column_key, filter_dependency_maps) ==> "recce.util.lineage.build_column_key", "recce.util.lineage.filter_dependency_maps"

    """

    def __init__(self) -> None:
        self.imports: set[str] = set()
        self.depth = 0  # top-level

    def get_full_dotted_name(self, expr: cst.BaseExpression) -> str:
        if isinstance(expr, cst.Name):
            return expr.value
        if isinstance(expr, cst.Attribute):
            return f"{self.get_full_dotted_name(expr.value)}.{expr.attr.value}"
        return ""

    def _collect_imports_from_block(self, block: cst.IndentedBlock) -> None:
        for statement in block.body:
            if isinstance(statement, cst.SimpleStatementLine):
                for child in statement.body:
                    if isinstance(child, cst.Import):
                        for alias in child.names:
                            module = self.get_full_dotted_name(alias.name)
                            asname = (
                                alias.asname.name.value
                                if alias.asname
                                else alias.name.value
                            )
                            if isinstance(asname, cst.Attribute):
                                self.imports.add(module)
                            else:
                                self.imports.add(
                                    module if module == asname else f"{module}.{asname}"
                                )

                    elif isinstance(child, cst.ImportFrom):
                        if child.module is None:
                            continue
                        module = self.get_full_dotted_name(child.module)
                        if isinstance(child.names, cst.ImportStar):
                            continue
                        for alias in child.names:
                            if isinstance(alias, cst.ImportAlias):
                                name = alias.name.value
                                asname = (
                                    alias.asname.name.value if alias.asname else name
                                )
                                self.imports.add(f"{module}.{asname}")

    def visit_Module(self, node: cst.Module) -> None:
        self.depth = 0
        self._collect_imports_from_block(node)

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:  # noqa: ARG002
        self.depth += 1

    def leave_FunctionDef(self, node: cst.FunctionDef) -> None:  # noqa: ARG002
        self.depth -= 1

    def visit_ClassDef(self, node: cst.ClassDef) -> None:  # noqa: ARG002
        self.depth += 1

    def leave_ClassDef(self, node: cst.ClassDef) -> None:  # noqa: ARG002
        self.depth -= 1

    def visit_If(self, node: cst.If) -> None:
        if self.depth == 0:
            self._collect_imports_from_block(node.body)

    def visit_Try(self, node: cst.Try) -> None:
        if self.depth == 0:
            self._collect_imports_from_block(node.body)


class ImportInserter(cst.CSTTransformer):
    """Transformer that inserts global statements after the last import."""

    def __init__(
        self, global_statements: list[cst.SimpleStatementLine], last_import_line: int
    ) -> None:
        super().__init__()
        self.global_statements = global_statements
        self.last_import_line = last_import_line
        self.current_line = 0
        self.inserted = False

    def leave_SimpleStatementLine(
        self,
        original_node: cst.SimpleStatementLine,  # noqa: ARG002
        updated_node: cst.SimpleStatementLine,
    ) -> cst.Module:
        self.current_line += 1

        # If we're right after the last import and haven't inserted yet
        if self.current_line == self.last_import_line and not self.inserted:
            self.inserted = True
            return cst.Module(body=[updated_node, *self.global_statements])

        return cst.Module(body=[updated_node])

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:  # noqa: ARG002
        # If there were no imports, add at the beginning of the module
        if self.last_import_line == 0 and not self.inserted:
            updated_body = list(updated_node.body)
            for stmt in reversed(self.global_statements):
                updated_body.insert(0, stmt)
            return updated_node.with_changes(body=updated_body)
        return updated_node


def extract_global_statements(
    source_code: str,
) -> tuple[cst.Module, list[cst.SimpleStatementLine]]:
    """Extract global statements from source code."""
    module = cst.parse_module(source_code)
    collector = GlobalStatementCollector()
    module.visit(collector)
    return module, collector.global_statements


def find_last_import_line(target_code: str) -> int:
    """Find the line number of the last import statement."""
    module = cst.parse_module(target_code)
    finder = LastImportFinder()
    module.visit(finder)
    return finder.last_import_line


class FutureAliasedImportTransformer(cst.CSTTransformer):
    def leave_ImportFrom(
        self,
        original_node: cst.ImportFrom,  # noqa: ARG002
        updated_node: cst.ImportFrom,
    ) -> (
        cst.BaseSmallStatement
        | cst.FlattenSentinel[cst.BaseSmallStatement]
        | cst.RemovalSentinel
    ):
        import libcst.matchers as m

        if (
            (updated_node_module := updated_node.module)
            and updated_node_module.value == "__future__"
            and all(m.matches(name, m.ImportAlias()) for name in updated_node.names)
        ):
            if names := [name for name in updated_node.names if name.asname is None]:
                return updated_node.with_changes(names=names)
            return cst.RemoveFromParent()
        return updated_node
