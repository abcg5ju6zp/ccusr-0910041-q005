from __future__ import annotations

from collections.abc import Iterable
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
)

from sanic.models.handler_types import RouteHandler
from sanic.request.types import Request


if TYPE_CHECKING:
    from sanic import Sanic
    from sanic.blueprints import Blueprint


class HTTPMethodView:
    """项目内部接口说明。"""

    decorators: list[Callable[[Callable[..., Any]], Callable[..., Any]]] = []

    def __init_subclass__(
        cls,
        attach: Sanic | Blueprint | None = None,
        uri: str = "",
        methods: Iterable[str] = frozenset({"GET"}),
        host: str | None = None,
        strict_slashes: bool | None = None,
        version: int | None = None,
        name: str | None = None,
        stream: bool = False,
        version_prefix: str = "/v",
        **kwargs: Any,
    ) -> None:
        super().__init_subclass__(**kwargs)
        if attach:
            cls.attach(
                attach,
                uri=uri,
                methods=methods,
                host=host,
                strict_slashes=strict_slashes,
                version=version,
                name=name,
                stream=stream,
                version_prefix=version_prefix,
            )

    def dispatch_request(self, request: Request, *args, **kwargs):
        """项目内部接口说明。"""
        method = request.method.lower()
        handler = getattr(self, method, None)

        if not handler and method == "head":
            handler = getattr(self, "get")
        if not handler:
            # The router will never allow us to get here, but this is
            # included as a fallback and for completeness.
            raise NotImplementedError(
                f"{request.method} is not supported for this endpoint."
            )
        return handler(request, *args, **kwargs)

    @classmethod
    def as_view(cls, *class_args: Any, **class_kwargs: Any) -> RouteHandler:
        """项目内部接口说明。"""

        def view(*args, **kwargs):
            self = view.view_class(*class_args, **class_kwargs)
            return self.dispatch_request(*args, **kwargs)

        if cls.decorators:
            view.__module__ = cls.__module__
            for decorator in cls.decorators:
                view = decorator(view)

        view.view_class = cls  # type: ignore
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.__name__ = cls.__name__
        return view

    @classmethod
    def attach(
        cls,
        to: Sanic | Blueprint,
        uri: str,
        methods: Iterable[str] = frozenset({"GET"}),
        host: str | None = None,
        strict_slashes: bool | None = None,
        version: int | None = None,
        name: str | None = None,
        stream: bool = False,
        version_prefix: str = "/v",
    ) -> None:
        """项目内部接口说明。"""
        to.add_route(
            cls.as_view(),
            uri=uri,
            methods=methods,
            host=host,
            strict_slashes=strict_slashes,
            version=version,
            name=name,
            stream=stream,
            version_prefix=version_prefix,
        )


def stream(func):
    """项目内部接口说明。"""
    func.is_stream = True
    return func
