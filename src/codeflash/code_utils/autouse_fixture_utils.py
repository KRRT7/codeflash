from __future__ import annotations

from pathlib import Path

import libcst as cst
from libcst.metadata import PositionProvider

from codeflash.code_utils.config_parser import find_conftest_files
from codeflash.code_utils.formatter import sort_imports
from codeflash.code_utils.line_profile_utils import ImportAdder


class AddRequestArgument(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        for decorator in original_node.decorators:
            dec = decorator.decorator
            if isinstance(dec, cst.Call):
                func_name = ""
                if isinstance(dec.func, cst.Attribute) and isinstance(
                    dec.func.value, cst.Name
                ):
                    if (
                        dec.func.attr.value == "fixture"
                        and dec.func.value.value == "pytest"
                    ):
                        func_name = "pytest.fixture"
                elif isinstance(dec.func, cst.Name) and dec.func.value == "fixture":
                    func_name = "fixture"
                if func_name:
                    for arg in dec.args:
                        if (
                            arg.keyword
                            and arg.keyword.value == "autouse"
                            and isinstance(arg.value, cst.Name)
                            and arg.value.value == "True"
                        ):
                            args = updated_node.params.params
                            arg_names = {arg.name.value for arg in args}
                            if "request" in arg_names:
                                return updated_node
                            request_param = cst.Param(name=cst.Name("request"))
                            if args:
                                first_arg = args[0].name.value
                                if first_arg in {"self", "cls"}:
                                    new_params = [args[0], request_param] + list(
                                        args[1:]
                                    )
                                else:
                                    new_params = [request_param] + list(args)
                            else:
                                new_params = [request_param]
                            new_param_list = updated_node.params.with_changes(
                                params=new_params
                            )
                            return updated_node.with_changes(params=new_param_list)
        return updated_node


class PytestMarkAdder(cst.CSTTransformer):
    def __init__(self, mark_name: str) -> None:
        super().__init__()
        self.mark_name = mark_name
        self.has_pytest_import = False

    def visit_Module(self, node: cst.Module) -> None:
        for statement in node.body:
            if isinstance(statement, cst.SimpleStatementLine):
                for stmt in statement.body:
                    if isinstance(stmt, cst.Import):
                        for import_alias in stmt.names:
                            if (
                                isinstance(import_alias, cst.ImportAlias)
                                and import_alias.name.value == "pytest"
                            ):
                                self.has_pytest_import = True

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if not self.has_pytest_import:
            import_stmt = cst.SimpleStatementLine(
                body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("pytest"))])]
            )
            updated_node = updated_node.with_changes(
                body=[import_stmt, *updated_node.body]
            )
        return updated_node

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        for decorator in updated_node.decorators:
            if self._is_pytest_mark(decorator.decorator, self.mark_name):
                return updated_node
        mark_decorator = self._create_pytest_mark()
        new_decorators = [*list(updated_node.decorators), mark_decorator]
        return updated_node.with_changes(decorators=new_decorators)

    def _is_pytest_mark(self, decorator: cst.BaseExpression, mark_name: str) -> bool:
        if isinstance(decorator, cst.Attribute):
            if (
                isinstance(decorator.value, cst.Attribute)
                and isinstance(decorator.value.value, cst.Name)
                and decorator.value.value.value == "pytest"
                and decorator.value.attr.value == "mark"
                and decorator.attr.value == mark_name
            ):
                return True
        elif isinstance(decorator, cst.Call) and isinstance(
            decorator.func, cst.Attribute
        ):
            return self._is_pytest_mark(decorator.func, mark_name)
        return False

    def _create_pytest_mark(self) -> cst.Decorator:
        mark_attr = cst.Attribute(
            value=cst.Attribute(value=cst.Name("pytest"), attr=cst.Name("mark")),
            attr=cst.Name(self.mark_name),
        )
        return cst.Decorator(decorator=mark_attr)


class AutouseFixtureModifier(cst.CSTTransformer):
    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        for decorator in original_node.decorators:
            dec = decorator.decorator
            if isinstance(dec, cst.Call):
                func_name = ""
                if isinstance(dec.func, cst.Attribute) and isinstance(
                    dec.func.value, cst.Name
                ):
                    if (
                        dec.func.attr.value == "fixture"
                        and dec.func.value.value == "pytest"
                    ):
                        func_name = "pytest.fixture"
                elif isinstance(dec.func, cst.Name) and dec.func.value == "fixture":
                    func_name = "fixture"
                if func_name:
                    for arg in dec.args:
                        if (
                            arg.keyword
                            and arg.keyword.value == "autouse"
                            and isinstance(arg.value, cst.Name)
                            and arg.value.value == "True"
                        ):
                            else_block = cst.Else(body=updated_node.body)
                            if_test = cst.parse_expression(
                                'request.node.get_closest_marker("codeflash_no_autouse")'
                            )
                            yield_statement = cst.parse_statement("yield")
                            if_body = cst.IndentedBlock(body=[yield_statement])
                            new_if_statement = cst.If(
                                test=if_test, body=if_body, orelse=else_block
                            )
                            return updated_node.with_changes(
                                body=cst.IndentedBlock(body=[new_if_statement])
                            )
        return updated_node


def disable_autouse(test_path: Path) -> str:
    file_content = test_path.read_text(encoding="utf-8")
    module = cst.parse_module(file_content)
    add_request_argument = AddRequestArgument()
    disable_autouse_fixture = AutouseFixtureModifier()
    modified_module = module.visit(add_request_argument)
    modified_module = modified_module.visit(disable_autouse_fixture)
    test_path.write_text(modified_module.code, encoding="utf-8")
    return file_content


def modify_autouse_fixture(test_paths: list[Path]) -> dict[Path, str]:
    file_content_map = {}
    conftest_files = find_conftest_files(test_paths)
    for cf_file in conftest_files:
        original_content = disable_autouse(cf_file)
        file_content_map[cf_file] = original_content
    return file_content_map


def add_custom_marker_to_all_tests(test_paths: list[Path]) -> None:
    for test_path in test_paths:
        file_content = test_path.read_text(encoding="utf-8")
        module = cst.parse_module(file_content)
        importadder = ImportAdder("import pytest")
        modified_module = module.visit(importadder)
        modified_module = cst.parse_module(
            sort_imports(code=modified_module.code, float_to_top=True)
        )
        pytest_mark_adder = PytestMarkAdder("codeflash_no_autouse")
        modified_module = modified_module.visit(pytest_mark_adder)
        test_path.write_text(modified_module.code, encoding="utf-8")
