from codeflash.models.config import AppConfig
from pathlib import Path

from codeflash.context.optimization_context import get_code_optimization_context
from codeflash.discovery.functions_to_optimize import FunctionToOptimize
from codeflash.models.domain import FunctionParent
from codeflash.optimization.optimizer import Optimizer


def test_benchmark_extract(benchmark) -> None:
    file_path = Path(__file__).parent.parent.parent.resolve() / "src" / "codeflash"
    opt = Optimizer(
        AppConfig(
            project_root=file_path.resolve(),
            module_root=Path("."),
            tests_root=(file_path.parent.parent / "tests").resolve(),
            pytest_cmd="pytest",
            test_project_root=Path.cwd(),
        )
    )
    function_to_optimize = FunctionToOptimize(
        function_name="replace_function_and_helpers_with_optimized_code",
        file_path=file_path / "optimization" / "function_optimizer.py",
        parents=[FunctionParent(name="FunctionOptimizer", type="ClassDef")],
        starting_line=None,
        ending_line=None,
    )

    benchmark(
        get_code_optimization_context, function_to_optimize, opt.config.project_root
    )
