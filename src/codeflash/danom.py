from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


class Result(ABC, Generic[T, E]):
    """Result monad. Consists of ``Ok`` and ``Err``."""

    @abstractmethod
    def is_ok(self) -> bool: ...

    def is_err(self) -> bool:
        return not self.is_ok()

    @abstractmethod
    def map(self, func: Callable[[T], U]) -> Result[U, E]: ...

    @abstractmethod
    def map_err(self, func: Callable[[E], F]) -> Result[T, F]: ...

    @abstractmethod
    def and_then(self, func: Callable[[T], Result[U, E]]) -> Result[U, E]: ...

    @abstractmethod
    def or_else(self, func: Callable[[E], Result[T, F]]) -> Result[T, F]: ...

    @abstractmethod
    def unwrap(self) -> T: ...

    @abstractmethod
    def unwrap_or(self, default: T) -> T: ...


class Ok(Result[T, E]):
    __match_args__ = ("inner",)

    def __init__(self, inner: T = None) -> None:
        self.inner = inner

    def is_ok(self) -> bool:
        return True

    def map(self, func: Callable[[T], U]) -> Ok[U]:
        return Ok(func(self.inner))

    def map_err(self, func: Callable[[E], F]) -> Ok[T]:
        return self

    def and_then(self, func: Callable[[T], Result[U, E]]) -> Result[U, E]:
        result = func(self.inner)
        while isinstance(result, Ok) and isinstance(result.inner, Result):
            result = result.inner
        return result

    def or_else(self, func: Callable[[E], Result[T, F]]) -> Ok[T]:
        return self

    def unwrap(self) -> T:
        return self.inner

    def unwrap_or(self, default: T) -> T:
        return self.inner

    def __repr__(self) -> str:
        return f"Ok({self.inner!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Ok):
            return NotImplemented
        return self.inner == other.inner

    def __hash__(self) -> int:
        return hash(("Ok", self.inner))


class Err(Result[T, E]):
    __match_args__ = ("error",)

    def __init__(self, error: E = None) -> None:
        self.error = error

    def is_ok(self) -> bool:
        return False

    def map(self, func: Callable[[T], U]) -> Err[E]:
        return self

    def map_err(self, func: Callable[[E], F]) -> Err[F]:
        return Err(func(self.error))

    def and_then(self, func: Callable[[T], Result[U, E]]) -> Err[E]:
        return self

    def or_else(self, func: Callable[[E], Result[T, F]]) -> Result[T, F]:
        result = func(self.error)
        while isinstance(result, Err) and isinstance(result.error, Result):
            result = result.error
        return result

    def unwrap(self) -> T:
        if isinstance(self.error, Exception):
            raise self.error
        msg = f"Cannot unwrap Err: {self.error}"
        raise ValueError(msg)

    def unwrap_or(self, default: T) -> T:
        return default

    def __repr__(self) -> str:
        return f"Err({self.error!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Err):
            return NotImplemented
        return type(self.error) is type(other.error) and str(self.error) == str(
            other.error
        )

    def __hash__(self) -> int:
        return hash(("Err", type(self.error), str(self.error)))
