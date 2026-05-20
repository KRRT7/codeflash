from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

import libcst as cst

from codeflash.cli_cmds.logging_config import logger


@dataclass
class UsageInfo:
    name: str
    used_by_qualified_function: bool = False
    dependencies: set[str] = field(default_factory=set)


def extract_names_from_targets(target: cst.CSTNode) -> list[str]:
    names = []
    if isinstance(target, cst.Name):
        names.append(target.value)
    elif hasattr(target, "value"):
        names.extend(extract_names_from_targets(target.value))
    elif hasattr(target, "elements"):
        for element in target.elements:
            names.extend(extract_names_from_targets(element))
    return names


def get_section_names(node: cst.CSTNode) -> list[str]:
    possible_sections = ["body", "orelse", "finalbody", "handlers"]
    return [sec for sec in possible_sections if hasattr(node, sec)]


def collect_top_level_definitions(
    node: cst.CSTNode, definitions: dict[str, UsageInfo] | None = None
) -> dict[str, UsageInfo]:
    FunctionDef = cst.FunctionDef
    ClassDef = cst.ClassDef
    Assign = cst.Assign
    AnnAssign = cst.AnnAssign
    AugAssign = cst.AugAssign
    IndentedBlock = cst.IndentedBlock

    if definitions is None:
        definitions = {}

    node_type = type(node)
    if node_type is FunctionDef:
        name = node.name.value
        definitions[name] = UsageInfo(
            name=name,
            used_by_qualified_function=False,
        )
        return definitions

    if node_type is ClassDef:
        name = node.name.value
        definitions[name] = UsageInfo(name=name)
        body = getattr(node, "body", None)
        if body is not None and type(body) is IndentedBlock:
            statements = body.body
            prefix = name + "."
            for statement in statements:
                if type(statement) is FunctionDef:
                    method_name = prefix + statement.name.value
                    definitions[method_name] = UsageInfo(name=method_name)
        return definitions

    if node_type is Assign:
        targets = node.targets
        append_def = definitions.__setitem__
        for target in targets:
            names = extract_names_from_targets(target.target)
            for name in names:
                append_def(name, UsageInfo(name=name))
        return definitions

    if node_type is AnnAssign or node_type is AugAssign:
        tgt = node.target
        if type(tgt) is cst.Name:
            name = tgt.value
            definitions[name] = UsageInfo(name=name)
        else:
            names = extract_names_from_targets(tgt)
            for name in names:
                definitions[name] = UsageInfo(name=name)
        return definitions

    section_names = get_section_names(node)
    if section_names:
        getattr_ = getattr
        for section in section_names:
            original_content = getattr_(node, section, None)
            if isinstance(original_content, (list, tuple)):
                defs = definitions
                for child in original_content:
                    collect_top_level_definitions(child, defs)
            elif original_content is not None:
                collect_top_level_definitions(original_content, definitions)
    return definitions


class DependencyCollector(cst.CSTVisitor):
    METADATA_DEPENDENCIES = (cst.metadata.ParentNodeProvider,)

    def __init__(self, definitions: dict[str, UsageInfo]) -> None:
        super().__init__()
        self.definitions = definitions
        self.function_depth = 0
        self.class_depth = 0
        self.current_top_level_name = ""
        self.current_class = ""
        self.processing_variable = False
        self.current_variable_names = set()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        function_name = node.name.value
        if self.function_depth == 0:
            if self.class_depth > 0:
                self.current_top_level_name = f"{self.current_class}.{function_name}"
            else:
                self.current_top_level_name = function_name
        if hasattr(node, "params") and node.params:
            for param in node.params.params:
                if param.annotation:
                    self._collect_annotation_dependencies(param.annotation)
        self.function_depth += 1

    def _collect_annotation_dependencies(self, annotation: cst.Annotation) -> None:
        if hasattr(annotation, "annotation"):
            self._extract_names_from_annotation(annotation.annotation)

    def _extract_names_from_annotation(self, node: cst.CSTNode) -> None:
        if isinstance(node, cst.Name):
            name = node.value
            if (
                name in self.definitions
                and name != self.current_top_level_name
                and self.current_top_level_name
            ):
                self.definitions[self.current_top_level_name].dependencies.add(name)
        elif isinstance(node, cst.Subscript):
            if hasattr(node, "value"):
                self._extract_names_from_annotation(node.value)
            if hasattr(node, "slice"):
                for slice_item in node.slice:
                    if hasattr(slice_item, "slice"):
                        self._extract_names_from_annotation(slice_item.slice)
        elif isinstance(node, cst.Attribute):
            if hasattr(node, "value"):
                self._extract_names_from_annotation(node.value)

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
        self.function_depth -= 1
        if self.function_depth == 0 and self.class_depth == 0:
            self.current_top_level_name = ""

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        class_name = node.name.value
        if self.class_depth == 0:
            self.current_class = class_name
            self.current_top_level_name = class_name
            for base in node.bases:
                if isinstance(base.value, cst.Name):
                    base_name = base.value.value
                    if base_name in self.definitions and class_name in self.definitions:
                        self.definitions[class_name].dependencies.add(base_name)
                elif isinstance(base.value, cst.Attribute):
                    attr_name = base.value.attr.value
                    if attr_name in self.definitions and class_name in self.definitions:
                        self.definitions[class_name].dependencies.add(attr_name)
        self.class_depth += 1

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        self.class_depth -= 1
        if self.class_depth == 0:
            self.current_class = ""
            self.current_top_level_name = ""

    def visit_Assign(self, node: cst.Assign) -> None:
        if self.function_depth == 0 and self.class_depth == 0:
            for target in node.targets:
                names = extract_names_from_targets(target.target)
                tracked_names = [name for name in names if name in self.definitions]
                if tracked_names:
                    self.processing_variable = True
                    self.current_variable_names.update(tracked_names)
                    self.current_top_level_name = tracked_names[0]

    def leave_Assign(self, original_node: cst.Assign) -> None:
        if self.processing_variable:
            self.processing_variable = False
            self.current_variable_names.clear()
            self.current_top_level_name = ""

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if hasattr(node, "annotation") and node.annotation:
            self.processing_variable = True
            if isinstance(node.target, cst.Name):
                self.current_variable_names.add(node.target.value)
            else:
                self.current_variable_names.update(
                    extract_names_from_targets(node.target)
                )
            self._collect_annotation_dependencies(node.annotation)
            self.processing_variable = False
            self.current_variable_names.clear()

    def visit_Name(self, node: cst.Name) -> None:
        name = node.value
        if (
            not self.current_top_level_name
            or self.current_top_level_name not in self.definitions
        ):
            return
        if self.processing_variable and name in self.current_variable_names:
            return
        if name in self.definitions and name != self.current_top_level_name:
            if self.class_depth > 0:
                parent = self.get_metadata(cst.metadata.ParentNodeProvider, node)
                if parent is not None and isinstance(parent, cst.Attribute):
                    return
            self.definitions[self.current_top_level_name].dependencies.add(name)


class QualifiedFunctionUsageMarker:
    def __init__(
        self, definitions: dict[str, UsageInfo], qualified_function_names: set[str]
    ) -> None:
        self.definitions = definitions
        self.qualified_function_names = qualified_function_names
        self.expanded_qualified_functions = self._expand_qualified_functions()

    def _expand_qualified_functions(self) -> set[str]:
        expanded = set(self.qualified_function_names)
        for qualified_name in list(self.qualified_function_names):
            if "." in qualified_name:
                class_name, _method_name = qualified_name.split(".", 1)
                expanded.add(class_name)
                for name in self.definitions:
                    if name.startswith(f"{class_name}.__") and name.endswith("__"):
                        expanded.add(name)
        return expanded

    def mark_used_definitions(self) -> None:
        expanded_names = self.expanded_qualified_functions
        defs = self.definitions
        fnames = (
            expanded_names & defs.keys()
            if isinstance(expanded_names, set)
            else [name for name in expanded_names if name in defs]
        )
        for func_name in fnames:
            defs[func_name].used_by_qualified_function = True
            for dep in defs[func_name].dependencies:
                self.mark_as_used_recursively(dep)

    def mark_as_used_recursively(self, name: str) -> None:
        if name not in self.definitions:
            return
        if self.definitions[name].used_by_qualified_function:
            return
        self.definitions[name].used_by_qualified_function = True
        for dep in self.definitions[name].dependencies:
            self.mark_as_used_recursively(dep)


def remove_unused_definitions_recursively(
    node: cst.CSTNode, definitions: dict[str, UsageInfo]
) -> tuple[cst.CSTNode | None, bool]:
    if isinstance(node, (cst.Import, cst.ImportFrom)):
        return node, True

    if isinstance(node, cst.FunctionDef):
        return node, True

    if isinstance(node, cst.ClassDef):
        class_name = node.name.value
        method_or_var_used = False
        class_has_dependencies = False

        if (
            class_name in definitions
            and definitions[class_name].used_by_qualified_function
        ):
            class_has_dependencies = True

        if hasattr(node, "body") and isinstance(node.body, cst.IndentedBlock):
            updates = {}
            new_statements = []

            for statement in node.body.body:
                if isinstance(statement, cst.FunctionDef):
                    method_name = f"{class_name}.{statement.name.value}"
                    if (
                        method_name in definitions
                        and definitions[method_name].used_by_qualified_function
                    ):
                        method_or_var_used = True
                    new_statements.append(statement)
                elif isinstance(statement, (cst.Assign, cst.AnnAssign, cst.AugAssign)):
                    var_used = False
                    if isinstance(statement, cst.Assign):
                        for target in statement.targets:
                            names = extract_names_from_targets(target.target)
                            for name in names:
                                class_var_name = f"{class_name}.{name}"
                                if (
                                    class_var_name in definitions
                                    and definitions[
                                        class_var_name
                                    ].used_by_qualified_function
                                ):
                                    var_used = True
                                    method_or_var_used = True
                                    break
                    elif isinstance(statement, (cst.AnnAssign, cst.AugAssign)):
                        names = extract_names_from_targets(statement.target)
                        for name in names:
                            class_var_name = f"{class_name}.{name}"
                            if (
                                class_var_name in definitions
                                and definitions[
                                    class_var_name
                                ].used_by_qualified_function
                            ):
                                var_used = True
                                method_or_var_used = True
                                break
                    if var_used or class_has_dependencies:
                        new_statements.append(statement)
                else:
                    new_statements.append(statement)

            new_body = node.body.with_changes(body=new_statements)
            updates["body"] = new_body
            return node.with_changes(**updates), True

        return node, method_or_var_used or class_has_dependencies

    if isinstance(node, cst.Assign):
        for target in node.targets:
            names = extract_names_from_targets(target.target)
            for name in names:
                if name in definitions and definitions[name].used_by_qualified_function:
                    return node, True
        return None, False

    if isinstance(node, (cst.AnnAssign, cst.AugAssign)):
        names = extract_names_from_targets(node.target)
        for name in names:
            if name in definitions and definitions[name].used_by_qualified_function:
                return node, True
        return None, False

    section_names = get_section_names(node)
    if not section_names:
        return node, False

    updates = {}
    found_used = False

    for section in section_names:
        original_content = getattr(node, section, None)
        if isinstance(original_content, (list, tuple)):
            new_children = []
            section_found_used = False

            for child in original_content:
                filtered, used = remove_unused_definitions_recursively(
                    child, definitions
                )
                if filtered:
                    new_children.append(filtered)
                section_found_used |= used

            if new_children or section_found_used:
                found_used |= section_found_used
                updates[section] = new_children
        elif original_content is not None:
            filtered, used = remove_unused_definitions_recursively(
                original_content, definitions
            )
            found_used |= used
            if filtered:
                updates[section] = filtered
    if not found_used:
        return None, False
    if updates:
        return node.with_changes(**updates), found_used
    return node, False


def collect_top_level_defs_with_usages(
    code: Union[str, cst.Module], qualified_function_names: set[str]
) -> dict[str, UsageInfo]:
    module = code if isinstance(code, cst.Module) else cst.parse_module(code)
    definitions = collect_top_level_definitions(module)
    wrapper = cst.MetadataWrapper(module)
    dependency_collector = DependencyCollector(definitions)
    wrapper.visit(dependency_collector)
    usage_marker = QualifiedFunctionUsageMarker(definitions, qualified_function_names)
    usage_marker.mark_used_definitions()
    return definitions


def remove_unused_definitions_by_function_names(
    code: str, qualified_function_names: set[str]
) -> str:
    try:
        module = cst.parse_module(code)
    except Exception as e:
        logger.debug(f"Failed to parse code with libcst: {type(e).__name__}: {e}")
        return code

    try:
        defs_with_usages = collect_top_level_defs_with_usages(
            module, qualified_function_names
        )
        modified_module, _ = remove_unused_definitions_recursively(
            module, defs_with_usages
        )
        return modified_module.code if modified_module else ""
    except Exception as e:
        logger.debug(
            f"Error processing code to remove unused definitions: {type(e).__name__}: {e}"
        )
        return code


def print_definitions(definitions: dict[str, UsageInfo]) -> None:
    print(f"Found {len(definitions)} definitions:")
    for name, info in sorted(definitions.items()):
        print(f"  - Name: {name}")
        print(f"    Used by qualified function: {info.used_by_qualified_function}")
        print(
            f"    Dependencies: {', '.join(sorted(info.dependencies)) if info.dependencies else 'None'}"
        )
        print()
