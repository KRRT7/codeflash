from __future__ import annotations

from itertools import chain

import libcst as cst


class GlobalAssignmentCollector(cst.CSTVisitor):
    def __init__(self) -> None:
        super().__init__()
        self.assignments: dict[str, cst.Assign] = {}
        self.assignment_order: list[str] = []
        self.scope_depth = 0
        self.if_else_depth = 0

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool | None:
        self.scope_depth += 1
        return True

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
        self.scope_depth -= 1

    def visit_ClassDef(self, node: cst.ClassDef) -> bool | None:
        self.scope_depth += 1
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        self.scope_depth -= 1

    def visit_If(self, node: cst.If) -> bool | None:
        self.if_else_depth += 1
        return True

    def leave_If(self, original_node: cst.If) -> None:
        self.if_else_depth -= 1

    def visit_Else(self, node: cst.Else) -> bool | None:
        return True

    def visit_Assign(self, node: cst.Assign) -> bool | None:
        if self.scope_depth == 0 and self.if_else_depth == 0:
            for target in node.targets:
                if isinstance(target.target, cst.Name):
                    name = target.target.value
                    self.assignments[name] = node
                    if name not in self.assignment_order:
                        self.assignment_order.append(name)
        return True


def find_insertion_index_after_imports(node: cst.Module) -> int:
    insert_index = 0
    for i, stmt in enumerate(node.body):
        is_top_level_import = isinstance(stmt, cst.SimpleStatementLine) and any(
            isinstance(child, (cst.Import, cst.ImportFrom)) for child in stmt.body
        )
        is_conditional_import = isinstance(stmt, cst.If) and all(
            isinstance(inner, cst.SimpleStatementLine)
            and all(
                isinstance(child, (cst.Import, cst.ImportFrom)) for child in inner.body
            )
            for inner in stmt.body.body
        )
        if is_top_level_import or is_conditional_import:
            insert_index = i + 1
        if isinstance(stmt, (cst.ClassDef, cst.FunctionDef)):
            break
    return insert_index


class GlobalAssignmentTransformer(cst.CSTTransformer):
    def __init__(
        self, new_assignments: dict[str, cst.Assign], new_assignment_order: list[str]
    ) -> None:
        super().__init__()
        self.new_assignments = new_assignments
        self.new_assignment_order = new_assignment_order
        self.processed_assignments: set[str] = set()
        self.scope_depth = 0
        self.if_else_depth = 0

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self.scope_depth += 1

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self.scope_depth -= 1
        return updated_node

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self.scope_depth += 1

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self.scope_depth -= 1
        return updated_node

    def visit_If(self, node: cst.If) -> None:
        self.if_else_depth += 1

    def leave_If(self, original_node: cst.If, updated_node: cst.If) -> cst.If:
        self.if_else_depth -= 1
        return updated_node

    def visit_Else(self, node: cst.Else) -> None:
        pass

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.CSTNode:
        if self.scope_depth > 0 or self.if_else_depth > 0:
            return updated_node
        for target in original_node.targets:
            if isinstance(target.target, cst.Name):
                name = target.target.value
                if name in self.new_assignments:
                    self.processed_assignments.add(name)
                    return self.new_assignments[name]
        return updated_node

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        new_statements = list(updated_node.body)
        assignments_to_append = [
            self.new_assignments[name]
            for name in self.new_assignment_order
            if name not in self.processed_assignments and name in self.new_assignments
        ]
        if assignments_to_append:
            insert_index = find_insertion_index_after_imports(updated_node)
            assignment_lines = [
                cst.SimpleStatementLine([assignment], leading_lines=[cst.EmptyLine()])
                for assignment in assignments_to_append
            ]
            new_statements = list(
                chain(
                    new_statements[:insert_index],
                    assignment_lines,
                    new_statements[insert_index:],
                )
            )
            after_index = insert_index + len(assignment_lines)
            if after_index < len(new_statements):
                next_stmt = new_statements[after_index]
                has_empty = any(
                    isinstance(line, cst.EmptyLine) for line in next_stmt.leading_lines
                )
                if not has_empty:
                    new_statements[after_index] = next_stmt.with_changes(
                        leading_lines=[cst.EmptyLine(), *next_stmt.leading_lines]
                    )
        return updated_node.with_changes(body=new_statements)
