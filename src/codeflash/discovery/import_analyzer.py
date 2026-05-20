from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

from codeflash.cli_cmds.logging_config import logger

if TYPE_CHECKING:
    from codeflash.models.domain import TestsInFile


FUNCTION_NAME_REGEX = re.compile(r"([^.]+)\.([a-zA-Z0-9_]+)$")


class ImportAnalyzer(ast.NodeVisitor):
    """AST-based analyzer to check if any qualified names from function_names_to_find are imported or used in a test file."""

    def __init__(self, function_names_to_find: set[str]) -> None:
        self.function_names_to_find = function_names_to_find
        self.found_any_target_function: bool = False
        self.found_qualified_name = None
        self.imported_modules: set[str] = set()
        self.has_dynamic_imports: bool = False
        self.wildcard_modules: set[str] = set()
        self.alias_mapping: dict[str, str] = {}
        self.instance_mapping: dict[str, str] = {}

        self._exact_names = function_names_to_find
        self._prefix_roots: dict[str, list[str]] = {}
        self._dot_names: set[str] = set()
        self._dot_methods: dict[str, set[str]] = {}
        self._class_method_to_target: dict[tuple[str, str], str] = {}

        add_dot_methods = self._dot_methods.setdefault
        add_prefix_roots = self._prefix_roots.setdefault
        dot_names_add = self._dot_names.add
        class_method_to_target = self._class_method_to_target
        for name in function_names_to_find:
            if "." in name:
                root, method = name.rsplit(".", 1)
                dot_names_add(name)
                add_dot_methods(method, set()).add(root)
                class_method_to_target[(root, method)] = name
                root_prefix = name.split(".", 1)[0]
                add_prefix_roots(root_prefix, []).append(name)

    def visit_Import(self, node: ast.Import) -> None:
        if self.found_any_target_function:
            return

        for alias in node.names:
            module_name = alias.asname if alias.asname else alias.name
            self.imported_modules.add(module_name)

            if alias.name == "importlib":
                self.has_dynamic_imports = True

            if module_name in self.function_names_to_find:
                self.found_any_target_function = True
                self.found_qualified_name = module_name  # type: ignore[assignment]
                return
            for target_func in self.function_names_to_find:
                if target_func.startswith(f"{module_name}."):
                    self.found_any_target_function = True
                    self.found_qualified_name = target_func  # type: ignore[assignment]
                    return

    def visit_Assign(self, node: ast.Assign) -> None:
        if self.found_any_target_function:
            return

        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            class_name = value.func.id
            if class_name in self.imported_modules:
                original_class = self.alias_mapping.get(class_name, class_name)
                targets = node.targets
                instance_mapping = self.instance_mapping
                for target in targets:
                    if isinstance(target, ast.Name):
                        instance_mapping[target.id] = original_class

        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self.found_any_target_function:
            return

        mod = node.module
        if not mod:
            return

        fnames = self._exact_names
        proots = self._prefix_roots

        for alias in node.names:
            aname = alias.name
            if aname == "*":
                self.wildcard_modules.add(mod)
                continue

            imported_name = alias.asname if alias.asname else aname
            self.imported_modules.add(imported_name)

            if alias.asname:
                self.alias_mapping[imported_name] = aname

            if mod == "importlib" and aname == "import_module":
                self.has_dynamic_imports = True

            qname = f"{mod}.{aname}"

            if aname in fnames:
                self.found_any_target_function = True
                self.found_qualified_name = aname  # type: ignore[assignment]
                return
            if qname in fnames:
                self.found_any_target_function = True
                self.found_qualified_name = qname  # type: ignore[assignment]
                return

            for target_func in fnames:
                if "." in target_func:
                    class_name, _method_name = target_func.split(".", 1)
                    if aname == class_name and not alias.asname:
                        self.found_any_target_function = True
                        self.found_qualified_name = target_func  # type: ignore[assignment]
                        return

            prefix = qname + "."
            candidates = proots.get(qname, ())
            for target_func in candidates:
                if target_func.startswith(prefix):
                    self.found_any_target_function = True
                    self.found_qualified_name = target_func  # type: ignore[assignment]
                    return

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.found_any_target_function:
            return

        node_value = node.value
        node_attr = node.attr

        val_id = getattr(node_value, "id", None)
        if val_id is not None and val_id in self.imported_modules:
            if node_attr in self.function_names_to_find:
                self.found_any_target_function = True
                self.found_qualified_name = node_attr  # type: ignore[assignment]
                return
            roots_possible = self._dot_methods.get(node_attr)
            if roots_possible:
                imported_name = val_id
                original_name = self.alias_mapping.get(imported_name, imported_name)
                if original_name in roots_possible:
                    self.found_any_target_function = True
                    self.found_qualified_name = self._class_method_to_target[  # type: ignore[assignment]
                        (original_name, node_attr)
                    ]
                    return
                if imported_name in roots_possible:
                    self.found_any_target_function = True
                    self.found_qualified_name = self._class_method_to_target.get(  # type: ignore[assignment]
                        (imported_name, node_attr), f"{imported_name}.{node_attr}"
                    )
                    return

        if val_id is not None and val_id in self.instance_mapping:
            class_name = self.instance_mapping[val_id]
            roots_possible = self._dot_methods.get(node_attr)
            if roots_possible and class_name in roots_possible:
                self.found_any_target_function = True
                self.found_qualified_name = self._class_method_to_target[  # type: ignore[assignment]
                    (class_name, node_attr)
                ]
                return

        if self.has_dynamic_imports and node_attr in self.function_names_to_find:
            self.found_any_target_function = True
            self.found_qualified_name = node_attr  # type: ignore[assignment]
            return

        if not self.found_any_target_function:
            ast.NodeVisitor.generic_visit(self, node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.found_any_target_function:
            return

        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            self.has_dynamic_imports = True

        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if self.found_any_target_function:
            return

        if node.id == "__import__":
            self.has_dynamic_imports = True

        if node.id in self.function_names_to_find:
            self.found_any_target_function = True
            self.found_qualified_name = node.id  # type: ignore[assignment]
            return

        for wildcard_module in self.wildcard_modules:
            for target_func in self.function_names_to_find:
                if target_func.startswith(
                    f"{wildcard_module}."
                ) and target_func.endswith(f".{node.id}"):
                    self.found_any_target_function = True
                    self.found_qualified_name = target_func  # type: ignore[assignment]
                    return

        self.generic_visit(node)

    def generic_visit(self, node: ast.AST) -> None:
        if self.found_any_target_function:
            return
        self._fast_generic_visit(node)

    def _fast_generic_visit(self, node: ast.AST) -> None:
        if self.found_any_target_function:
            return

        visit_cache = type(self).__dict__
        node_fields = node._fields

        stack = [(node_fields, node)]
        append = stack.append
        pop = stack.pop

        while stack:
            fields, curr_node = pop()
            for field in fields:
                value = getattr(curr_node, field, None)
                if isinstance(value, list):
                    for item in value:
                        if self.found_any_target_function:
                            return
                        if isinstance(item, ast.AST):
                            meth = visit_cache.get("visit_" + item.__class__.__name__)
                            if meth is not None:
                                meth(self, item)
                            else:
                                append((item._fields, item))
                    continue
                if isinstance(value, ast.AST):
                    if self.found_any_target_function:
                        return
                    meth = visit_cache.get("visit_" + value.__class__.__name__)
                    if meth is not None:
                        meth(self, value)
                    else:
                        append((value._fields, value))


def analyze_imports_in_test_file(
    test_file_path: Path | str, target_functions: set[str]
) -> bool:
    try:
        with Path(test_file_path).open("r", encoding="utf-8") as f:
            source_code = f.read()
        tree = ast.parse(source_code, filename=str(test_file_path))
        analyzer = ImportAnalyzer(target_functions)
        analyzer.visit(tree)
    except (SyntaxError, FileNotFoundError) as e:
        logger.debug(f"Failed to analyze imports in {test_file_path}: {e}")
        return True

    if analyzer.found_any_target_function:
        return True

    if analyzer.has_dynamic_imports:
        for target_func in target_functions:
            if target_func in source_code:
                return True

    return False


def filter_test_files_by_imports(
    file_to_test_map: dict[Path, list[TestsInFile]], target_functions: set[str]
) -> dict[Path, list[TestsInFile]]:
    if not target_functions:
        return file_to_test_map

    filtered_map = {}
    for test_file, test_functions in file_to_test_map.items():
        should_process = analyze_imports_in_test_file(test_file, target_functions)
        if should_process:
            filtered_map[test_file] = test_functions

    logger.debug(
        f"analyzed {len(file_to_test_map)} test files for imports, filtered down to {len(filtered_map)} relevant files"
    )
    return filtered_map
