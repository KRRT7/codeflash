from __future__ import annotations

from pathlib import Path

import libcst as cst

from codeflash.cli_cmds.logging_config import logger
from codeflash.code_utils.formatter import sort_imports
from codeflash.discovery.functions_to_optimize import FunctionToOptimize
from codeflash.models.coverage import TestingMode


class AsyncDecoratorAdder(cst.CSTTransformer):
    def __init__(
        self, function: FunctionToOptimize, mode: TestingMode = TestingMode.BEHAVIOR
    ) -> None:
        super().__init__()
        self.function = function
        self.mode = mode
        self.qualified_name_parts = function.qualified_name.split(".")
        self.context_stack: list[str] = []
        self.added_decorator = False
        self.decorator_name = (
            "codeflash_behavior_async"
            if mode == TestingMode.BEHAVIOR
            else "codeflash_performance_async"
        )

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self.context_stack.append(node.name.value)

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self.context_stack.pop()
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self.context_stack.append(node.name.value)

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        if (
            original_node.asynchronous is not None
            and self.context_stack == self.qualified_name_parts
        ):
            has_decorator = any(
                self._is_target_decorator(decorator.decorator)  # type: ignore[arg-type]
                for decorator in original_node.decorators
            )
            if not has_decorator:
                new_decorator = cst.Decorator(
                    decorator=cst.Name(value=self.decorator_name)
                )
                updated_decorators = [new_decorator, *list(updated_node.decorators)]
                updated_node = updated_node.with_changes(
                    decorators=tuple(updated_decorators)
                )
                self.added_decorator = True
        self.context_stack.pop()
        return updated_node

    def _is_target_decorator(
        self, decorator_node: cst.Name | cst.Attribute | cst.Call
    ) -> bool:
        target_names = {
            "codeflash_trace_async",
            "codeflash_behavior_async",
            "codeflash_performance_async",
        }
        if isinstance(decorator_node, cst.Name):
            return decorator_node.value in target_names
        if isinstance(decorator_node, cst.Call) and isinstance(
            decorator_node.func, cst.Name
        ):
            return decorator_node.func.value in target_names
        return False


class AsyncDecoratorImportAdder(cst.CSTTransformer):
    def __init__(self, mode: TestingMode = TestingMode.BEHAVIOR) -> None:
        self.mode = mode
        self.has_import = False

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if (
            isinstance(node.module, cst.Attribute)
            and isinstance(node.module.value, cst.Attribute)
            and isinstance(node.module.value.value, cst.Name)
            and node.module.value.value.value == "codeflash"
            and node.module.value.attr.value == "code_utils"
            and node.module.attr.value == "codeflash_wrap_decorator"
            and not isinstance(node.names, cst.ImportStar)
        ):
            decorator_name = (
                "codeflash_behavior_async"
                if self.mode == TestingMode.BEHAVIOR
                else "codeflash_performance_async"
            )
            for import_alias in node.names:
                if import_alias.name.value == decorator_name:
                    self.has_import = True

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if self.has_import:
            return updated_node
        decorator_name = (
            "codeflash_behavior_async"
            if self.mode == TestingMode.BEHAVIOR
            else "codeflash_performance_async"
        )
        import_node = cst.parse_statement(
            f"from codeflash.code_utils.codeflash_wrap_decorator import {decorator_name}"
        )
        return updated_node.with_changes(body=[import_node, *list(updated_node.body)])


def add_async_decorator_to_function(
    source_path: Path,
    function: FunctionToOptimize,
    mode: TestingMode = TestingMode.BEHAVIOR,
) -> bool:
    if not function.is_async:
        return False
    try:
        with source_path.open(encoding="utf8") as f:
            source_code = f.read()
        module = cst.parse_module(source_code)
        decorator_transformer = AsyncDecoratorAdder(function, mode)
        module = module.visit(decorator_transformer)
        if decorator_transformer.added_decorator:
            import_transformer = AsyncDecoratorImportAdder(mode)
            module = module.visit(import_transformer)
        modified_code = sort_imports(code=module.code, float_to_top=True)
    except Exception as e:
        logger.exception(
            f"Error adding async decorator to function {function.qualified_name}: {e}"
        )
        return False
    else:
        if decorator_transformer.added_decorator:
            with source_path.open("w", encoding="utf8") as f:
                f.write(modified_code)
            logger.debug(f"Applied async {mode.value} instrumentation to {source_path}")
            return True
        return False
