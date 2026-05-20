from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import (
    AddImportsVisitor,
    GatherImportsVisitor,
    RemoveImportsVisitor,
)
from libcst.helpers import calculate_module_and_package

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.cst_import_utils import (
    DottedImportCollector,
    FutureAliasedImportTransformer,
    ImportInserter,
    extract_global_statements,
    find_last_import_line,
)
from codeflash.code_utils.global_assignment_utils import (
    GlobalAssignmentCollector,
    GlobalAssignmentTransformer,
)

if TYPE_CHECKING:
    from libcst.helpers import ModuleNameAndPackage

    from codeflash.models.domain import FunctionSource


def delete___future___aliased_imports(module_code: str) -> str:
    return cst.parse_module(module_code).visit(FutureAliasedImportTransformer()).code


def add_global_assignments(src_module_code: str, dst_module_code: str) -> str:
    src_module, new_added_global_statements = extract_global_statements(src_module_code)
    dst_module, existing_global_statements = extract_global_statements(dst_module_code)
    unique_global_statements = []
    for stmt in new_added_global_statements:
        if any(
            stmt is existing_stmt or stmt.deep_equals(existing_stmt)
            for existing_stmt in existing_global_statements
        ):
            continue
        unique_global_statements.append(stmt)
    mod_dst_code = dst_module_code
    if unique_global_statements:
        last_import_line = find_last_import_line(dst_module_code)
        import_transformer: cst.CSTTransformer = ImportInserter(
            unique_global_statements, last_import_line
        )
        modified_module = dst_module.visit(import_transformer)
        mod_dst_code = modified_module.code
        original_module = cst.parse_module(mod_dst_code)
    else:
        original_module = dst_module
    new_collector = GlobalAssignmentCollector()
    src_module.visit(new_collector)
    if not new_collector.assignments:
        return mod_dst_code
    transformer: cst.CSTTransformer = GlobalAssignmentTransformer(
        new_collector.assignments, new_collector.assignment_order
    )
    transformed_module = original_module.visit(transformer)
    return transformed_module.code


def resolve_star_import(module_name: str, project_root: Path) -> set[str]:
    try:
        module_path = module_name.replace(".", "/")
        possible_paths = [
            project_root / f"{module_path}.py",
            project_root / f"{module_path}/__init__.py",
        ]
        module_file = None
        for path in possible_paths:
            if path.exists():
                module_file = path
                break
        if module_file is None:
            logger.warning(
                f"Could not find module file for {module_name}, skipping star import resolution"
            )
            return set()
        with module_file.open(encoding="utf8") as f:
            module_code = f.read()
        tree = ast.parse(module_code)
        all_names = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__all__"
            ):
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    all_names = [
                        elt.value
                        for elt in node.value.elts
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                    ]
                    break
        if all_names is not None:
            return set(all_names)
        public_names = set()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.name.startswith("_"):
                    public_names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_"):
                        public_names.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name) and not node.target.id.startswith(
                    "_"
                ):
                    public_names.add(node.target.id)
            elif isinstance(node, ast.Import) or (
                isinstance(node, ast.ImportFrom)
                and not any(alias.name == "*" for alias in node.names)
            ):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if not name.startswith("_"):
                        public_names.add(name)
        return public_names
    except Exception as e:
        logger.warning(f"Error resolving star import for {module_name}: {e}")
        return set()


def add_needed_imports_from_module(
    src_module_code: str,
    dst_module_code: str,
    src_path: Path,
    dst_path: Path,
    project_root: Path,
    helper_functions: list[FunctionSource] | None = None,
    helper_functions_fqn: set[str] | None = None,
) -> str:
    src_module_code = delete___future___aliased_imports(src_module_code)
    if not helper_functions_fqn:
        helper_functions_fqn = {
            f.fully_qualified_name for f in (helper_functions or [])
        }
    src_module_and_package: ModuleNameAndPackage = calculate_module_and_package(
        project_root, src_path
    )
    dst_module_and_package: ModuleNameAndPackage = calculate_module_and_package(
        project_root, dst_path
    )
    dst_context: CodemodContext = CodemodContext(
        filename=src_path.name,
        full_module_name=dst_module_and_package.name,
        full_package_name=dst_module_and_package.package,
    )
    gatherer: GatherImportsVisitor = GatherImportsVisitor(
        CodemodContext(
            filename=src_path.name,
            full_module_name=src_module_and_package.name,
            full_package_name=src_module_and_package.package,
        )
    )
    try:
        cst.parse_module(src_module_code).visit(gatherer)
    except Exception as e:
        logger.error(f"Error parsing source module code: {e}")
        return dst_module_code
    dotted_import_collector = DottedImportCollector()
    try:
        parsed_dst_module = cst.parse_module(dst_module_code)
        parsed_dst_module.visit(dotted_import_collector)
    except cst.ParserSyntaxError as e:
        logger.exception(f"Syntax error in destination module code: {e}")
        return dst_module_code
    try:
        for mod in gatherer.module_imports:
            if mod == "__future__":
                continue
            if mod not in dotted_import_collector.imports:
                AddImportsVisitor.add_needed_import(dst_context, mod)
            RemoveImportsVisitor.remove_unused_import(dst_context, mod)
        aliased_objects = set()
        for mod, alias_pairs in gatherer.alias_mapping.items():
            for alias_pair in alias_pairs:
                if alias_pair[0] and alias_pair[1]:
                    aliased_objects.add(f"{mod}.{alias_pair[0]}")
        for mod, obj_seq in gatherer.object_mapping.items():
            for obj in obj_seq:
                if (
                    f"{mod}.{obj}" in helper_functions_fqn
                    or dst_context.full_module_name == mod
                ):
                    continue
                if f"{mod}.{obj}" in aliased_objects:
                    continue
                if obj == "*":
                    resolved_symbols = resolve_star_import(mod, project_root)
                    logger.debug(f"Resolved star import from {mod}: {resolved_symbols}")
                    for symbol in resolved_symbols:
                        if (
                            f"{mod}.{symbol}" not in helper_functions_fqn
                            and f"{mod}.{symbol}" not in dotted_import_collector.imports
                        ):
                            AddImportsVisitor.add_needed_import(
                                dst_context, mod, symbol
                            )
                        RemoveImportsVisitor.remove_unused_import(
                            dst_context, mod, symbol
                        )
                else:
                    if f"{mod}.{obj}" not in dotted_import_collector.imports:
                        AddImportsVisitor.add_needed_import(dst_context, mod, obj)
                    RemoveImportsVisitor.remove_unused_import(dst_context, mod, obj)
    except Exception as e:
        logger.exception(f"Error adding imports to destination module code: {e}")
        return dst_module_code
    for mod, asname in gatherer.module_aliases.items():
        if not asname:
            continue
        if f"{mod}.{asname}" not in dotted_import_collector.imports:
            AddImportsVisitor.add_needed_import(dst_context, mod, asname=asname)
        RemoveImportsVisitor.remove_unused_import(dst_context, mod, asname=asname)
    for mod, alias_pairs in gatherer.alias_mapping.items():
        for alias_pair in alias_pairs:
            if f"{mod}.{alias_pair[0]}" in helper_functions_fqn:
                continue
            if not alias_pair[0] or not alias_pair[1]:
                continue
            if f"{mod}.{alias_pair[1]}" not in dotted_import_collector.imports:
                AddImportsVisitor.add_needed_import(
                    dst_context, mod, alias_pair[0], asname=alias_pair[1]
                )
            RemoveImportsVisitor.remove_unused_import(
                dst_context, mod, alias_pair[0], asname=alias_pair[1]
            )
    try:
        add_imports_visitor = AddImportsVisitor(dst_context)
        transformed_module = add_imports_visitor.transform_module(parsed_dst_module)
        transformed_module = RemoveImportsVisitor(dst_context).transform_module(
            transformed_module
        )
        return transformed_module.code.lstrip("\n")
    except Exception as e:
        logger.exception(f"Error adding imports to destination module code: {e}")
        return dst_module_code
