from __future__ import annotations

import ast


JIT_DECORATORS: dict[str, set[str]] = {
    "numba": {
        "jit",
        "njit",
        "vectorize",
        "guvectorize",
        "stencil",
        "cfunc",
        "generated_jit",
    },
    "numba.cuda": {"jit"},
    "torch": {"compile"},
    "torch.jit": {"script", "trace"},
    "tensorflow": {"function"},
    "jax": {"jit"},
}


class JitDecoratorDetector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.import_aliases: dict[str, tuple[str, str | None]] = {}
        self.found_jit_decorator = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname if alias.asname else alias.name
            self.import_aliases[local_name] = (alias.name, None)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is None:
            self.generic_visit(node)
            return

        for alias in node.names:
            local_name = alias.asname if alias.asname else alias.name
            self.import_aliases[local_name] = (node.module, alias.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            if self._is_jit_decorator(decorator):
                self.found_jit_decorator = True
                return
        self.generic_visit(node)

    def _is_jit_decorator(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Call):
            return self._is_jit_decorator(node.func)
        if isinstance(node, ast.Name):
            return self._check_name_decorator(node.id)
        if isinstance(node, ast.Attribute):
            return self._check_attribute_decorator(node)
        return False

    def _check_name_decorator(self, name: str) -> bool:
        if name not in self.import_aliases:
            return False
        module, imported_name = self.import_aliases[name]
        if imported_name is None:
            return False
        return self._is_known_jit_decorator(module, imported_name)

    def _check_attribute_decorator(self, node: ast.Attribute) -> bool:
        parts = self._get_attribute_parts(node)
        if not parts:
            return False
        first_part = parts[0]
        rest_parts = parts[1:]
        if first_part in self.import_aliases:
            module, imported_name = self.import_aliases[first_part]
            if imported_name is None:
                if rest_parts:
                    full_module = module
                    decorator_name = rest_parts[-1]
                    if len(rest_parts) > 1:
                        full_module = f"{module}.{'.'.join(rest_parts[:-1])}"
                    return self._is_known_jit_decorator(full_module, decorator_name)
            elif rest_parts:
                full_module = f"{module}.{imported_name}"
                decorator_name = rest_parts[-1]
                if len(rest_parts) > 1:
                    full_module = f"{full_module}.{'.'.join(rest_parts[:-1])}"
                return self._is_known_jit_decorator(full_module, decorator_name)
        elif rest_parts:
            full_module = first_part
            if len(rest_parts) > 1:
                full_module = f"{first_part}.{'.'.join(rest_parts[:-1])}"
            decorator_name = rest_parts[-1]
            return self._is_known_jit_decorator(full_module, decorator_name)
        return False

    def _get_attribute_parts(self, node: ast.Attribute) -> list[str]:
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            parts.reverse()
            return parts
        return []

    def _is_known_jit_decorator(self, module: str, decorator_name: str) -> bool:
        if module in JIT_DECORATORS:
            return decorator_name in JIT_DECORATORS[module]
        return False


def contains_jit_decorator(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    detector = JitDecoratorDetector()
    detector.visit(tree)
    return detector.found_jit_decorator
