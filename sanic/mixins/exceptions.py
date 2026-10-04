from __future__ import annotations

from typing import Any, Callable

from sanic.base.meta import SanicMeta
from sanic.models.futures import FutureException


class ExceptionMixin(metaclass=SanicMeta):
    def __init__(self, *args, **kwargs) -> None:
        self._future_exceptions: set[FutureException] = set()

    def _apply_exception_handler(self, handler: FutureException):
        raise NotImplementedError  # noqa

    def exception(
        self,
        *exceptions: type[Exception] | list[type[Exception]],
        apply: bool = True,
    ) -> Callable:
        """项目内部接口说明。"""

        def decorator(handler):
            nonlocal apply
            nonlocal exceptions

            if isinstance(exceptions[0], list):
                exceptions = tuple(*exceptions)

            future_exception = FutureException(handler, exceptions)
            self._future_exceptions.add(future_exception)
            if apply:
                self._apply_exception_handler(future_exception)
            return handler

        return decorator

    def all_exceptions(
        self, handler: Callable[..., Any]
    ) -> Callable[..., Any]:
        """项目内部接口说明。"""
        return self.exception(Exception)(handler)
