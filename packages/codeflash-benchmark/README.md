# codeflash-benchmark

A pytest plugin for benchmarking with [codeflash.ai](https://codeflash.ai).

## Overview

`codeflash-benchmark` is installed automatically as part of `codeflash` and provides a `benchmark` fixture that integrates with CodeFlash's performance tracing. It is compatible with `pytest-benchmark` — if that package is installed, its fixture is used by default; when `--codeflash-trace` is active, CodeFlash's own tracer takes over.

## Usage

Use the `benchmark` fixture in any test:

```python
def test_my_function(benchmark):
    result = benchmark(my_function, arg1, arg2)
    assert result == expected
```

When CodeFlash runs your test suite with `--codeflash-trace`:

- The CodeFlash tracer is used instead of `pytest-benchmark`
- All `--benchmark-*` options are ignored
- The `benchmark` marker identifies tests for tracing

## Markers

```python
@pytest.mark.benchmark
def test_performance():
    ...
```

## pytest-benchmark compatibility

If `pytest-benchmark` is already installed, `codeflash-benchmark` defers to it unless `--codeflash-trace` is active. This means existing benchmark suites work without modification.
