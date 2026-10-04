from __future__ import annotations

from collections.abc import Coroutine
from enum import Enum
from typing import Any, Callable

from sanic.base.meta import SanicMeta
from sanic.models.futures import FutureSignal
from sanic.models.handler_types import SignalHandler
from sanic.signals import Event, Signal
from sanic.types import HashableDict


class SignalMixin(metaclass=SanicMeta):
    def __init__(self, *args, **kwargs) -> None:
        self._future_signals: set[FutureSignal] = set()

    def _apply_signal(self, signal: FutureSignal) -> Signal:
        raise NotImplementedError  # noqa

    def signal(
        self,
        event: str | Enum,
        *,
        apply: bool = True,
        condition: dict[str, Any] | None = None,
        exclusive: bool = True,
        priority: int = 0,
    ) -> Callable[[SignalHandler], SignalHandler]:
        """项目内部接口说明。"""
        event_value = str(event.value) if isinstance(event, Enum) else event

        def decorator(handler: SignalHandler):
            future_signal = FutureSignal(
                handler,
                event_value,
                HashableDict(condition or {}),
                exclusive,
                priority,
            )
            self._future_signals.add(future_signal)

            if apply:
                self._apply_signal(future_signal)

            return handler

        return decorator

    def add_signal(
        self,
        handler: Callable[..., Any] | None,
        event: str | Enum,
        condition: dict[str, Any] | None = None,
        exclusive: bool = True,
    ) -> Callable[..., Any]:
        """项目内部接口说明。"""
        if not handler:

            async def noop(**context): ...

            handler = noop
        self.signal(event=event, condition=condition, exclusive=exclusive)(
            handler
        )
        return handler

    def event(self, event: str):
        raise NotImplementedError

    def catch_exception(
        self,
        handler: Callable[[SignalMixin, Exception], Coroutine[Any, Any, None]],
    ) -> None:
        """项目内部接口说明。"""

        async def signal_handler(exception: Exception):
            await handler(self, exception)

        self.signal(Event.SERVER_EXCEPTION_REPORT)(signal_handler)
