from __future__ import annotations

from pathlib import Path

import libcst as cst
from pydantic.dataclasses import dataclass

from codeflash._constants import VerificationType
from codeflash.models.test_type import TestType


@dataclass(frozen=True)
class InvocationId:
    test_module_path: str
    test_class_name: str | None
    test_function_name: str | None
    function_getting_tested: str
    iteration_id: str | None

    def id(self) -> str:
        class_prefix = f"{self.test_class_name}." if self.test_class_name else ""
        return f"{self.test_module_path}:{class_prefix}{self.test_function_name}:{self.function_getting_tested}:{self.iteration_id}"

    def test_fn_qualified_name(self) -> str:
        return (
            f"{self.test_class_name}.{self.test_function_name}"
            if self.test_class_name
            else str(self.test_function_name)
        )

    def find_func_in_class(
        self, class_node: cst.ClassDef, func_name: str
    ) -> cst.FunctionDef | None:
        for stmt in class_node.body.body:
            if isinstance(stmt, cst.FunctionDef) and stmt.name.value == func_name:
                return stmt
        return None

    def get_src_code(self, test_path: Path) -> str | None:
        if not test_path.exists():
            return None
        try:
            test_src = test_path.read_text(encoding="utf-8")
            module_node = cst.parse_module(test_src)
        except Exception:
            return None
        if self.test_class_name:
            for stmt in module_node.body:
                if (
                    isinstance(stmt, cst.ClassDef)
                    and stmt.name.value == self.test_class_name
                ):
                    if self.test_function_name is None:
                        return None
                    func_node = self.find_func_in_class(stmt, self.test_function_name)
                    if func_node:
                        return module_node.code_for_node(func_node).strip()
            return None
        for stmt in module_node.body:
            if (
                isinstance(stmt, cst.FunctionDef)
                and stmt.name.value == self.test_function_name
            ):
                return module_node.code_for_node(stmt).strip()
        return None

    @staticmethod
    def from_str_id(string_id: str, iteration_id: str | None = None) -> InvocationId:
        components = string_id.split(":")
        assert len(components) == 4
        second_components = components[1].split(".")
        if len(second_components) == 1:
            return InvocationId(
                test_module_path=components[0],
                test_class_name=None,
                test_function_name=second_components[0],
                function_getting_tested=components[2],
                iteration_id=iteration_id if iteration_id else components[3],
            )
        return InvocationId(
            test_module_path=components[0],
            test_class_name=second_components[0],
            test_function_name=second_components[1],
            function_getting_tested=components[2],
            iteration_id=iteration_id if iteration_id else components[3],
        )


@dataclass(frozen=True)
class FunctionTestInvocation:
    loop_index: int
    id: InvocationId
    file_name: Path
    did_pass: bool
    runtime: int | None
    test_framework: str
    test_type: TestType
    return_value: object | None
    timed_out: bool | None
    verification_type: str | None = VerificationType.FUNCTION_CALL
    stdout: str | None = None

    @property
    def unique_invocation_loop_id(self) -> str:
        return f"{self.loop_index}:{self.id.id()}"
