from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any



benchmark_context_cleaner = re.compile(r"[^a-zA-Z0-9_]+")


def get_function_alias(module: str, function_name: str) -> str:
    return "_".join(module.split(".")) + "_" + function_name


def get_unique_test_name(
    module: str, function_name: str, benchmark_name: str, class_name: str | None = None
) -> str:
    clean_benchmark = benchmark_context_cleaner.sub("_", benchmark_name).strip("_")
    base_alias = get_function_alias(module, function_name)
    if class_name:
        return f"{get_function_alias(module, class_name)}_{function_name}_{clean_benchmark}"
    return f"{base_alias}_{clean_benchmark}"


def create_trace_replay_test_code(
    trace_file: str, functions_data: list[dict[str, Any]], max_run_count: int = 256
) -> str:
    imports = "from codeflash.picklepatch.pickle_patcher import PicklePatcher as pickle\nfrom codeflash.benchmarking.replay_test import get_next_arg_and_return\n"
    function_imports = []
    for func in functions_data:
        module_name = func.get("module_name")
        function_name = func.get("function_name")
        class_name = func.get("class_name", "")
        if class_name:
            function_imports.append(
                f"from {module_name} import {class_name} as {get_function_alias(module_name, class_name)}"
            )
        else:
            function_imports.append(
                f"from {module_name} import {function_name} as {get_function_alias(module_name, function_name)}"
            )
    imports += "\n".join(function_imports)
    functions_to_optimize = sorted(
        {
            func.get("function_name")
            for func in functions_data
            if func.get("function_name") != "__init__"
        }
    )
    metadata = (
        f'functions = {functions_to_optimize}\ntrace_file_path = r"{trace_file}"\n'
    )
    test_function_body = textwrap.dedent("""\
        for args_pkl, kwargs_pkl in get_next_arg_and_return(trace_file=trace_file_path, benchmark_function_name="{benchmark_function_name}", function_name="{orig_function_name}", file_path=r"{file_path}", num_to_get={max_run_count}):
            args = pickle.loads(args_pkl)
            kwargs = pickle.loads(kwargs_pkl)
            ret = {function_name}(*args, **kwargs)
            """)
    test_method_body = textwrap.dedent("""\
        for args_pkl, kwargs_pkl in get_next_arg_and_return(trace_file=trace_file_path, benchmark_function_name="{benchmark_function_name}", function_name="{orig_function_name}", file_path=r"{file_path}", class_name="{class_name}", num_to_get={max_run_count}):
            args = pickle.loads(args_pkl)
            kwargs = pickle.loads(kwargs_pkl){filter_variables}
            function_name = "{orig_function_name}"
            if not args:
                raise ValueError("No arguments provided for the method.")
            if function_name == "__init__":
                ret = {class_name_alias}(*args[1:], **kwargs)
            else:
                ret = {class_name_alias}{method_name}(*args, **kwargs)
            """)
    test_class_method_body = textwrap.dedent("""\
        for args_pkl, kwargs_pkl in get_next_arg_and_return(trace_file=trace_file_path, benchmark_function_name="{benchmark_function_name}", function_name="{orig_function_name}", file_path=r"{file_path}", class_name="{class_name}", num_to_get={max_run_count}):
            args = pickle.loads(args_pkl)
            kwargs = pickle.loads(kwargs_pkl){filter_variables}
            if not args:
                raise ValueError("No arguments provided for the method.")
            ret = {class_name_alias}{method_name}(*args[1:], **kwargs)
            """)
    test_static_method_body = textwrap.dedent("""\
        for args_pkl, kwargs_pkl in get_next_arg_and_return(trace_file=trace_file_path, benchmark_function_name="{benchmark_function_name}", function_name="{orig_function_name}", file_path=r"{file_path}", class_name="{class_name}", num_to_get={max_run_count}):
            args = pickle.loads(args_pkl)
            kwargs = pickle.loads(kwargs_pkl){filter_variables}
            ret = {class_name_alias}{method_name}(*args, **kwargs)
            """)
    test_template = ""
    for func in functions_data:
        module_name = func.get("module_name")
        function_name = func.get("function_name")
        class_name = func.get("class_name")
        file_path = Path(func.get("file_path")).as_posix()
        benchmark_function_name = func.get("benchmark_function_name")
        function_properties = func.get("function_properties")
        if not class_name:
            alias = get_function_alias(module_name, function_name)
            test_body = test_function_body.format(
                benchmark_function_name=benchmark_function_name,
                orig_function_name=function_name,
                function_name=alias,
                file_path=file_path,
                max_run_count=max_run_count,
            )
        else:
            class_name_alias = get_function_alias(module_name, class_name)
            filter_variables = ""
            method_name = "." + function_name if function_name != "__init__" else ""
            if function_properties.is_classmethod:
                test_body = test_class_method_body.format(
                    benchmark_function_name=benchmark_function_name,
                    orig_function_name=function_name,
                    file_path=file_path,
                    class_name_alias=class_name_alias,
                    class_name=class_name,
                    method_name=method_name,
                    max_run_count=max_run_count,
                    filter_variables=filter_variables,
                )
            elif function_properties.is_staticmethod:
                test_body = test_static_method_body.format(
                    benchmark_function_name=benchmark_function_name,
                    orig_function_name=function_name,
                    file_path=file_path,
                    class_name_alias=class_name_alias,
                    class_name=class_name,
                    method_name=method_name,
                    max_run_count=max_run_count,
                    filter_variables=filter_variables,
                )
            else:
                test_body = test_method_body.format(
                    benchmark_function_name=benchmark_function_name,
                    orig_function_name=function_name,
                    file_path=file_path,
                    class_name_alias=class_name_alias,
                    class_name=class_name,
                    method_name=method_name,
                    max_run_count=max_run_count,
                    filter_variables=filter_variables,
                )
        formatted_test_body = textwrap.indent(test_body, "    ")
        unique_test_name = get_unique_test_name(
            module_name, function_name, benchmark_function_name, class_name
        )
        test_template += f"def test_{unique_test_name}():\n{formatted_test_body}\n"
    return imports + "\n" + metadata + "\n" + test_template
