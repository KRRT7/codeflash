from __future__ import annotations

import ast
import time
from dataclasses import dataclass
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
from codeflash.models.domain import FunctionParent

from codeflash.code_utils.cst_import_utils import (
    GlobalAssignmentCollector,
    GlobalAssignmentTransformer,
    DottedImportCollector,
    ImportInserter,
    FutureAliasedImportTransformer,
)
from codeflash.code_utils.call_finder import (
    find_occurances,
    get_fn_references_jedi,
)
from codeflash.code_utils.cst_import_utils import extract_global_statements, find_last_import_line

if TYPE_CHECKING:
    from libcst.helpers import ModuleNameAndPackage

    from codeflash.discovery.functions_to_optimize import FunctionToOptimize
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
    # Insert unique global statements if any
    if unique_global_statements:
        last_import_line = find_last_import_line(dst_module_code)
        # Reuse already-parsed dst_module
        transformer = ImportInserter(unique_global_statements, last_import_line)
        # Use visit inplace, don't parse again
        modified_module = dst_module.visit(transformer)
        mod_dst_code = modified_module.code
        # Parse the code after insertion
        original_module = cst.parse_module(mod_dst_code)
    else:
        # No new statements to insert, reuse already-parsed dst_module
        original_module = dst_module

    # Parse the src_module_code once only (already done above: src_module)
    # Collect assignments from the new file
    new_collector = GlobalAssignmentCollector()
    src_module.visit(new_collector)
    # Only create transformer if there are assignments to insert/transform
    if not new_collector.assignments:  # nothing to transform
        return mod_dst_code

    # Transform the original destination module
    transformer = GlobalAssignmentTransformer(
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
                    all_names = []
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            all_names.append(elt.value)
                        elif isinstance(elt, ast.Str):  # Python < 3.8 compatibility
                            all_names.append(elt.s)
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

        return public_names  # noqa: TRY300

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
    """Add all needed and used source module code imports to the destination module code, and return it."""
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
        return dst_module_code  # Return the original code if there's a syntax error

    try:
        for mod in gatherer.module_imports:
            # Skip __future__ imports as they cannot be imported directly
            # __future__ imports should only be imported with specific objects i.e from __future__ import annotations
            if mod == "__future__":
                continue
            if mod not in dotted_import_collector.imports:
                AddImportsVisitor.add_needed_import(dst_context, mod)
            RemoveImportsVisitor.remove_unused_import(dst_context, mod)
        aliased_objects = set()
        for mod, alias_pairs in gatherer.alias_mapping.items():
            for alias_pair in alias_pairs:
                if alias_pair[0] and alias_pair[1]:  # Both name and alias exist
                    aliased_objects.add(f"{mod}.{alias_pair[0]}")

        for mod, obj_seq in gatherer.object_mapping.items():
            for obj in obj_seq:
                if (
                    f"{mod}.{obj}" in helper_functions_fqn
                    or dst_context.full_module_name == mod  # avoid circular deps
                ):
                    continue  # Skip adding imports for helper functions already in the context

                if f"{mod}.{obj}" in aliased_objects:
                    continue

                # Handle star imports by resolving them to actual symbol names
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


def get_code(
    functions_to_optimize: list[FunctionToOptimize],
) -> tuple[str | None, set[tuple[str, str]]]:
    """Return the code for a function or methods in a Python module.

    functions_to_optimize is either a singleton FunctionToOptimize instance, which represents either a function at the
    module level or a method of a class at the module level, or it represents a list of methods of the same class.
    """
    if (
        not functions_to_optimize
        or (
            functions_to_optimize[0].parents
            and functions_to_optimize[0].parents[0].type != "ClassDef"
        )
        or (
            len(functions_to_optimize[0].parents) > 1
            or (
                (len(functions_to_optimize) > 1)
                and len({fn.parents[0] for fn in functions_to_optimize}) != 1
            )
        )
    ):
        return None, set()

    file_path: Path = functions_to_optimize[0].file_path
    class_skeleton: set[tuple[int, int | None]] = set()
    contextual_dunder_methods: set[tuple[str, str]] = set()
    target_code: str = ""

    def find_target(
        node_list: list[ast.stmt], name_parts: tuple[str, str] | tuple[str]
    ) -> ast.AST | None:
        target: (
            ast.FunctionDef
            | ast.AsyncFunctionDef
            | ast.ClassDef
            | ast.Assign
            | ast.AnnAssign
            | None
        ) = None
        node: ast.stmt
        for node in node_list:
            if (
                # The many mypy issues will be fixed once this code moves to the backend,
                # using Type Guards as we move to 3.10+.
                # We will cover the Type Alias case on the backend since it's a 3.12 feature.
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == name_parts[0]
            ):
                target = node
                break
                # The next two cases cover type aliases in pre-3.12 syntax, where only single assignment is allowed.
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name_parts[0]
            ) or (
                isinstance(node, ast.AnnAssign)
                and hasattr(node.target, "id")
                and node.target.id == name_parts[0]
            ):
                if class_skeleton:
                    break
                target = node
                break

        if target is None or len(name_parts) == 1:
            return target

        if not isinstance(target, ast.ClassDef) or len(name_parts) < 2:
            return None
        # At this point, name_parts has at least 2 elements
        method_name: str = name_parts[1]  # type: ignore[misc]
        class_skeleton.add((target.lineno, target.body[0].lineno - 1))
        cbody = target.body
        if isinstance(cbody[0], ast.expr):  # Is a docstring
            class_skeleton.add((cbody[0].lineno, cbody[0].end_lineno))
            cbody = cbody[1:]
            cnode: ast.stmt
        for cnode in cbody:
            # Collect all dunder methods.
            cnode_name: str
            if (
                isinstance(cnode, (ast.FunctionDef, ast.AsyncFunctionDef))
                and len(cnode_name := cnode.name) > 4
                and cnode_name != method_name
                and cnode_name.isascii()
                and cnode_name.startswith("__")
                and cnode_name.endswith("__")
            ):
                contextual_dunder_methods.add((target.name, cnode_name))
                class_skeleton.add((cnode.lineno, cnode.end_lineno))

        return find_target(target.body, (method_name,))

    with file_path.open(encoding="utf8") as file:
        source_code: str = file.read()
    try:
        module_node: ast.Module = ast.parse(source_code)
    except SyntaxError:
        logger.exception("get_code - Syntax error while parsing code")
        return None, set()
    # Get the source code lines for the target node
    lines: list[str] = source_code.splitlines(keepends=True)
    if len(functions_to_optimize[0].parents) == 1:
        if (
            functions_to_optimize[0].parents[0].type == "ClassDef"
        ):  # All functions_to_optimize functions are methods of the same class.
            qualified_name_parts_list: list[tuple[str, str] | tuple[str]] = [
                (fto.parents[0].name, fto.function_name)
                for fto in functions_to_optimize
            ]

        else:
            logger.error(
                f"Error: get_code does not support inner functions: {functions_to_optimize[0].parents}"
            )
            return None, set()
    elif len(functions_to_optimize[0].parents) == 0:
        qualified_name_parts_list = [(functions_to_optimize[0].function_name,)]
    else:
        logger.error(
            "Error: get_code does not support more than one level of nesting for now. "
            f"Parents: {functions_to_optimize[0].parents}"
        )
        return None, set()
    for qualified_name_parts in qualified_name_parts_list:
        target_node = find_target(module_node.body, qualified_name_parts)
        if target_node is None:
            continue
        # find_target returns FunctionDef, AsyncFunctionDef, ClassDef, Assign, or AnnAssign - all have lineno/end_lineno
        if not isinstance(
            target_node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
                ast.Assign,
                ast.AnnAssign,
            ),
        ):
            continue

        if (
            isinstance(
                target_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
            and target_node.decorator_list
        ):
            target_code += "".join(
                lines[target_node.decorator_list[0].lineno - 1 : target_node.end_lineno]
            )
        else:
            target_code += "".join(
                lines[target_node.lineno - 1 : target_node.end_lineno]
            )
    if not target_code:
        return None, set()
    class_list: list[tuple[int, int | None]] = sorted(class_skeleton)
    class_code = "".join(
        ["".join(lines[s_lineno - 1 : e_lineno]) for (s_lineno, e_lineno) in class_list]
    )
    return class_code + target_code, contextual_dunder_methods


def extract_code(
    functions_to_optimize: list[FunctionToOptimize],
) -> tuple[str | None, set[tuple[str, str]]]:
    edited_code, contextual_dunder_methods = get_code(functions_to_optimize)
    if edited_code is None:
        return None, set()
    try:
        compile(edited_code, "edited_code", "exec")
    except SyntaxError as e:
        logger.exception(
            f"extract_code - Syntax error in extracted optimization candidate code: {e}"
        )
        return None, set()
    return edited_code, contextual_dunder_methods


def find_preexisting_objects(
    source_code: str,
) -> set[tuple[str, tuple[FunctionParent, ...]]]:
    """Find all preexisting functions, classes or class methods in the source code."""
    preexisting_objects: set[tuple[str, tuple[FunctionParent, ...]]] = set()
    try:
        module_node: ast.Module = ast.parse(source_code)
    except SyntaxError:
        logger.exception("find_preexisting_objects - Syntax error while parsing code")
        return preexisting_objects
    for node in module_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            preexisting_objects.add((node.name, ()))
        elif isinstance(node, ast.ClassDef):
            preexisting_objects.add((node.name, ()))
            for cnode in node.body:
                if isinstance(cnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    preexisting_objects.add(
                        (cnode.name, (FunctionParent(node.name, "ClassDef"),))
                    )
    return preexisting_objects


@dataclass
def get_opt_review_metrics(
    source_code: str,
    file_path: Path,
    qualified_name: str,
    project_root: Path,
    tests_root: Path,
) -> str:
    start_time = time.perf_counter()
    try:
        qualified_name_split = qualified_name.rsplit(".", maxsplit=1)
        if len(qualified_name_split) == 1:
            target_function, target_class = qualified_name_split[0], None
        else:
            target_function, target_class = (
                qualified_name_split[1],
                qualified_name_split[0],
            )
        matches = get_fn_references_jedi(
            source_code, file_path, project_root, target_function, target_class
        )  # jedi is not perfect, it doesn't capture aliased references
        calling_fns_details = find_occurances(
            qualified_name, str(file_path), matches, project_root, tests_root
        )
    except Exception as e:
        calling_fns_details = ""
        logger.debug(f"Investigate {e}")
    end_time = time.perf_counter()
    logger.debug(f"Got function references in {end_time - start_time:.2f} seconds")
    return calling_fns_details
