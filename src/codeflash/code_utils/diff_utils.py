from __future__ import annotations

import difflib


def unified_diff_strings(
    code1: str, code2: str, fromfile: str = "original", tofile: str = "modified"
) -> str:
    code1_lines = code1.splitlines(keepends=True)
    code2_lines = code2.splitlines(keepends=True)
    diff = difflib.unified_diff(
        code1_lines, code2_lines, fromfile=fromfile, tofile=tofile, lineterm=""
    )
    return "".join(diff)


def diff_length(a: str, b: str) -> int:
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff_lines = list(difflib.unified_diff(a_lines, b_lines, lineterm=""))
    diff_text = "\n".join(diff_lines)
    return len(diff_text)


def choose_weights(**importance: float) -> list[float]:
    total = sum(importance.values())
    if total == 0:
        raise ValueError("At least one importance value must be > 0")
    return [v / total for v in importance.values()]


def normalize_by_max(values: list[float]) -> list[float]:
    mx = max(values)
    if mx == 0:
        return [0.0] * len(values)
    return [v / mx for v in values]


def create_score_dictionary_from_metrics(
    weights: list[float], *metrics: list[float]
) -> dict[int, float]:
    if len(weights) != len(metrics):
        raise ValueError("Number of weights must match number of metrics")
    combined: dict[int, float] = {}
    for weight, metric in zip(weights, metrics):
        for idx, value in enumerate(metric):
            combined[idx] = combined.get(idx, 0) + value * weight
    return combined


def create_rank_dictionary_compact(int_array: list[int]) -> dict[int, int]:
    sorted_indices = sorted(range(len(int_array)), key=lambda i: int_array[i])
    return {original_index: rank for rank, original_index in enumerate(sorted_indices)}


def encoded_tokens_len(s: str) -> int:
    return int(len(s) * 0.25)
