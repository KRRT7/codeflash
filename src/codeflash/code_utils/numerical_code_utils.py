from __future__ import annotations

import ast
from importlib.util import find_spec


has_numba = find_spec("numba") is not None

NUMERICAL_MODULES = frozenset(
    {"numpy", "torch", "numba", "jax", "tensorflow", "math", "scipy"}
)

NUMBA_REQUIRED_MODULES = frozenset({"numpy", "math", "scipy"})


class NumericalUsageChecker(ast.NodeVisitor):
    def __init__(self, numerical_names: set[str]) -> None:
        self.numerical_names = numerical_names
        self.found_numerical = False

    def visit_Call(self, node: ast.Call) -> None:
        if self.found_numerical:
            return
        call_name = self._get_root_name(node.func)
        if call_name and call_name in self.numerical_names:
            self.found_numerical = True
            return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.found_numerical:
            return
        root_name = self._get_root_name(node)
        if root_name and root_name in self.numerical_names:
            self.found_numerical = True
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.found_numerical:
            return
        if node.id in self.numerical_names:
            self.found_numerical = True

    def _get_root_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return self._get_root_name(node.value)
        return None


def _collect_numerical_imports(tree: ast.Module) -> tuple[set[str], set[str]]:
    numerical_names: set[str] = set()
    modules_used: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_root = alias.name.split(".")[0]
                if module_root in NUMERICAL_MODULES:
                    name = alias.asname if alias.asname else alias.name.split(".")[0]
                    numerical_names.add(name)
                    modules_used.add(module_root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_root = node.module.split(".")[0]
            if module_root in NUMERICAL_MODULES:
                for alias in node.names:
                    if alias.name == "*":
                        numerical_names.add(module_root)
                    else:
                        name = alias.asname if alias.asname else alias.name
                        numerical_names.add(name)
                modules_used.add(module_root)

    return numerical_names, modules_used


def _find_function_node(
    tree: ast.Module, name_parts: list[str]
) -> ast.FunctionDef | None:
    if not name_parts:
        return None
    if len(name_parts) == 1:
        func_name = name_parts[0]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == func_name:
                return node
        return None
    if len(name_parts) == 2:
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
    try:
        tree = ast.parse(code_string)
    except SyntaxError:
        return False

    name_parts = function_name.split(".")
    target_function = _find_function_node(tree, name_parts)
    if target_function is None:
        return False

    numerical_names, modules_used = _collect_numerical_imports(tree)
    checker = NumericalUsageChecker(numerical_names)
    checker.visit(target_function)

    if not checker.found_numerical:
        return False

    if not has_numba and modules_used.issubset(NUMBA_REQUIRED_MODULES):
        return False

    return True
