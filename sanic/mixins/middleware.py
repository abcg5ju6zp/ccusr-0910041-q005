from __future__ import annotations

from collections import deque
from functools import partial
from operator import attrgetter
from typing import Callable, overload

from sanic.base.meta import SanicMeta
from sanic.middleware import Middleware, MiddlewareLocation
from sanic.models.futures import FutureMiddleware, MiddlewareType
from sanic.router import Router


class MiddlewareMixin(metaclass=SanicMeta):
    router: Router

    def __init__(self, *args, **kwargs) -> None:
        self._future_middleware: list[FutureMiddleware] = []

    def _apply_middleware(self, middleware: FutureMiddleware):
        raise NotImplementedError  # noqa

    @overload
    def middleware(
        self,
        middleware_or_request: MiddlewareType,
        attach_to: str = "request",
        apply: bool = True,
        *,
        priority: int = 0,
    ) -> MiddlewareType: ...

    @overload
    def middleware(
        self,
        middleware_or_request: str,
        attach_to: str = "request",
        apply: bool = True,
        *,
        priority: int = 0,
    ) -> Callable[[MiddlewareType], MiddlewareType]: ...

    def middleware(
        self,
        middleware_or_request: MiddlewareType | str,
        attach_to: str = "request",
        apply: bool = True,
        *,
        priority: int = 0,
    ) -> MiddlewareType | Callable[[MiddlewareType], MiddlewareType]:
        """项目内部接口说明。"""

        def register_middleware(middleware, attach_to="request"):
            nonlocal apply

            location = (
                MiddlewareLocation.REQUEST
                if attach_to == "request"
                else MiddlewareLocation.RESPONSE
            )
            middleware = Middleware(middleware, location, priority=priority)
            future_middleware = FutureMiddleware(middleware, attach_to)
            self._future_middleware.append(future_middleware)
            if apply:
                self._apply_middleware(future_middleware)
            return middleware

        # Detect which way this was called, @middleware or @middleware('AT')
        if callable(middleware_or_request):
            return register_middleware(
                middleware_or_request, attach_to=attach_to
            )
        else:
            return partial(
                register_middleware, attach_to=middleware_or_request
            )

    def on_request(self, middleware=None, *, priority=0) -> MiddlewareType:
        """项目内部接口说明。"""
        if callable(middleware):
            return self.middleware(middleware, "request", priority=priority)
        else:
            return partial(  # type: ignore
                self.middleware, attach_to="request", priority=priority
            )

    def on_response(self, middleware=None, *, priority=0):
        """项目内部接口说明。"""
        if callable(middleware):
            return self.middleware(middleware, "response", priority=priority)
        else:
            return partial(
                self.middleware, attach_to="response", priority=priority
            )

    def finalize_middleware(self) -> None:
        """项目内部接口说明。"""
        for route in self.router.routes:
            request_middleware = Middleware.convert(
                self.request_middleware,  # type: ignore
                self.named_request_middleware.get(route.name, deque()),  # type: ignore  # noqa: E501
                location=MiddlewareLocation.REQUEST,
            )
            response_middleware = Middleware.convert(
                self.response_middleware,  # type: ignore
                self.named_response_middleware.get(route.name, deque()),  # type: ignore  # noqa: E501
                location=MiddlewareLocation.RESPONSE,
            )
            route.extra.request_middleware = deque(
                sorted(
                    request_middleware,
                    key=attrgetter("order"),
                    reverse=True,
                )
            )
            route.extra.response_middleware = deque(
                sorted(
                    response_middleware,
                    key=attrgetter("order"),
                    reverse=True,
                )[::-1]
            )
        request_middleware = Middleware.convert(
            self.request_middleware,  # type: ignore
            location=MiddlewareLocation.REQUEST,
        )
        response_middleware = Middleware.convert(
            self.response_middleware,  # type: ignore
            location=MiddlewareLocation.RESPONSE,
        )
        self.request_middleware = deque(
            sorted(
                request_middleware,
                key=attrgetter("order"),
                reverse=True,
            )
        )
        self.response_middleware = deque(
            sorted(
                response_middleware,
                key=attrgetter("order"),
                reverse=True,
            )[::-1]
        )
