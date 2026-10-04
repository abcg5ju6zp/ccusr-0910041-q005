from __future__ import annotations

from ast import NodeVisitor, Return, parse
from collections.abc import Iterable
from contextlib import suppress
from inspect import getsource, signature
from textwrap import dedent
from typing import (
    Any,
    Callable,
    cast,
)

from sanic_routing.route import Route

from sanic.base.meta import SanicMeta
from sanic.constants import HTTP_METHODS
from sanic.errorpages import RESPONSE_MAPPING
from sanic.mixins.base import BaseMixin
from sanic.models.futures import FutureRoute, FutureStatic
from sanic.models.handler_types import RouteHandler
from sanic.types import HashableDict


RouteWrapper = Callable[
    [RouteHandler], RouteHandler | tuple[Route, RouteHandler]
]


class RouteMixin(BaseMixin, metaclass=SanicMeta):
    def __init__(self, *args, **kwargs) -> None:
        self._future_routes: set[FutureRoute] = set()
        self._future_statics: set[FutureStatic] = set()

    def _apply_route(self, route: FutureRoute) -> list[Route]:
        raise NotImplementedError  # noqa

    def route(
        self,
        uri: str,
        methods: Iterable[str] | None = None,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        stream: bool = False,
        version: int | str | float | None = None,
        name: str | None = None,
        ignore_body: bool = False,
        apply: bool = True,
        subprotocols: list[str] | None = None,
        websocket: bool = False,
        unquote: bool = False,
        static: bool = False,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteWrapper:
        """项目内部接口说明。"""

        # Fix case where the user did not prefix the URL with a /
        # and will probably get confused as to why it's not working
        if not uri.startswith("/") and (uri or hasattr(self, "router")):
            uri = "/" + uri

        if strict_slashes is None:
            strict_slashes = self.strict_slashes

        if not methods and not websocket:
            methods = frozenset({"GET"})

        route_context = self._build_route_context(ctx_kwargs)

        def decorator(handler):
            nonlocal uri
            nonlocal methods
            nonlocal host
            nonlocal strict_slashes
            nonlocal stream
            nonlocal version
            nonlocal name
            nonlocal ignore_body
            nonlocal subprotocols
            nonlocal websocket
            nonlocal static
            nonlocal version_prefix
            nonlocal error_format

            if isinstance(handler, tuple):
                # if a handler fn is already wrapped in a route, the handler
                # variable will be a tuple of (existing routes, handler fn)
                _, handler = handler

            name = self.generate_name(name, handler)

            if isinstance(host, str):
                host = frozenset([host])
            elif host and not isinstance(host, frozenset):
                try:
                    host = frozenset(host)
                except TypeError:
                    raise ValueError(
                        "Expected either string or Iterable of host strings, "
                        "not %s" % host
                    )
            if isinstance(subprotocols, list):
                # Ordered subprotocols, maintain order
                subprotocols = tuple(subprotocols)
            elif isinstance(subprotocols, set):
                # subprotocol is unordered, keep it unordered
                subprotocols = frozenset(subprotocols)

            if not error_format or error_format == "auto":
                error_format = self._determine_error_format(handler)

            route = FutureRoute(
                handler,
                uri,
                None if websocket else frozenset([x.upper() for x in methods]),
                host,
                strict_slashes,
                stream,
                version,
                name,
                ignore_body,
                websocket,
                subprotocols,
                unquote,
                static,
                version_prefix,
                error_format,
                route_context,
            )
            overwrite = getattr(self, "_allow_route_overwrite", False)
            if overwrite:
                self._future_routes = set(
                    filter(lambda x: x.uri != uri, self._future_routes)
                )
            self._future_routes.add(route)

            args = list(signature(handler).parameters.keys())
            if websocket and len(args) < 2:
                handler_name = handler.__name__

                raise ValueError(
                    f"Required parameter `request` and/or `ws` missing "
                    f"in the {handler_name}() route?"
                )
            elif not args:
                handler_name = handler.__name__

                raise ValueError(
                    f"Required parameter `request` missing "
                    f"in the {handler_name}() route?"
                )

            if not websocket and stream:
                handler.is_stream = stream

            if apply:
                self._apply_route(route, overwrite=overwrite)

            if static:
                return route, handler
            return handler

        return decorator

    def add_route(
        self,
        handler: RouteHandler,
        uri: str,
        methods: Iterable[str] = frozenset({"GET"}),
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        version: int | str | float | None = None,
        name: str | None = None,
        stream: bool = False,
        version_prefix: str = "/v",
        error_format: str | None = None,
        unquote: bool = False,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        # Handle HTTPMethodView differently
        if hasattr(handler, "view_class"):
            methods = set()

            for method in HTTP_METHODS:
                view_class = getattr(handler, "view_class")
                _handler = getattr(view_class, method.lower(), None)
                if _handler:
                    methods.add(method)
                    if hasattr(_handler, "is_stream"):
                        stream = True

        if strict_slashes is None:
            strict_slashes = self.strict_slashes

        self.route(
            uri=uri,
            methods=methods,
            host=host,
            strict_slashes=strict_slashes,
            stream=stream,
            version=version,
            name=name,
            version_prefix=version_prefix,
            error_format=error_format,
            unquote=unquote,
            **ctx_kwargs,
        )(handler)
        return handler

    # Shorthand method decorators
    def get(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        version: int | str | float | None = None,
        name: str | None = None,
        ignore_body: bool = True,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        return cast(
            RouteHandler,
            self.route(
                uri,
                methods=frozenset({"GET"}),
                host=host,
                strict_slashes=strict_slashes,
                version=version,
                name=name,
                ignore_body=ignore_body,
                version_prefix=version_prefix,
                error_format=error_format,
                **ctx_kwargs,
            ),
        )

    def post(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        stream: bool = False,
        version: int | str | float | None = None,
        name: str | None = None,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        return cast(
            RouteHandler,
            self.route(
                uri,
                methods=frozenset({"POST"}),
                host=host,
                strict_slashes=strict_slashes,
                stream=stream,
                version=version,
                name=name,
                version_prefix=version_prefix,
                error_format=error_format,
                **ctx_kwargs,
            ),
        )

    def put(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        stream: bool = False,
        version: int | str | float | None = None,
        name: str | None = None,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        return cast(
            RouteHandler,
            self.route(
                uri,
                methods=frozenset({"PUT"}),
                host=host,
                strict_slashes=strict_slashes,
                stream=stream,
                version=version,
                name=name,
                version_prefix=version_prefix,
                error_format=error_format,
                **ctx_kwargs,
            ),
        )

    def head(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        version: int | str | float | None = None,
        name: str | None = None,
        ignore_body: bool = True,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        return cast(
            RouteHandler,
            self.route(
                uri,
                methods=frozenset({"HEAD"}),
                host=host,
                strict_slashes=strict_slashes,
                version=version,
                name=name,
                ignore_body=ignore_body,
                version_prefix=version_prefix,
                error_format=error_format,
                **ctx_kwargs,
            ),
        )

    def options(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        version: int | str | float | None = None,
        name: str | None = None,
        ignore_body: bool = True,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        return cast(
            RouteHandler,
            self.route(
                uri,
                methods=frozenset({"OPTIONS"}),
                host=host,
                strict_slashes=strict_slashes,
                version=version,
                name=name,
                ignore_body=ignore_body,
                version_prefix=version_prefix,
                error_format=error_format,
                **ctx_kwargs,
            ),
        )

    def patch(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        stream=False,
        version: int | str | float | None = None,
        name: str | None = None,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        return cast(
            RouteHandler,
            self.route(
                uri,
                methods=frozenset({"PATCH"}),
                host=host,
                strict_slashes=strict_slashes,
                stream=stream,
                version=version,
                name=name,
                version_prefix=version_prefix,
                error_format=error_format,
                **ctx_kwargs,
            ),
        )

    def delete(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        version: int | str | float | None = None,
        name: str | None = None,
        ignore_body: bool = False,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ) -> RouteHandler:
        """项目内部接口说明。"""
        return cast(
            RouteHandler,
            self.route(
                uri,
                methods=frozenset({"DELETE"}),
                host=host,
                strict_slashes=strict_slashes,
                version=version,
                name=name,
                ignore_body=ignore_body,
                version_prefix=version_prefix,
                error_format=error_format,
                **ctx_kwargs,
            ),
        )

    def websocket(
        self,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        subprotocols: list[str] | None = None,
        version: int | str | float | None = None,
        name: str | None = None,
        apply: bool = True,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ):
        """项目内部接口说明。"""
        return self.route(
            uri=uri,
            host=host,
            methods=None,
            strict_slashes=strict_slashes,
            version=version,
            name=name,
            apply=apply,
            subprotocols=subprotocols,
            websocket=True,
            version_prefix=version_prefix,
            error_format=error_format,
            **ctx_kwargs,
        )

    def add_websocket_route(
        self,
        handler,
        uri: str,
        host: str | list[str] | None = None,
        strict_slashes: bool | None = None,
        subprotocols=None,
        version: int | str | float | None = None,
        name: str | None = None,
        version_prefix: str = "/v",
        error_format: str | None = None,
        **ctx_kwargs: Any,
    ):
        """项目内部接口说明。"""
        return self.websocket(
            uri=uri,
            host=host,
            strict_slashes=strict_slashes,
            subprotocols=subprotocols,
            version=version,
            name=name,
            version_prefix=version_prefix,
            error_format=error_format,
            **ctx_kwargs,
        )(handler)

    def _determine_error_format(self, handler) -> str:
        with suppress(OSError, TypeError):
            src = dedent(getsource(handler))
            tree = parse(src)
            http_response_types = self._get_response_types(tree)

            if len(http_response_types) == 1:
                return next(iter(http_response_types))

        return ""

    def _get_response_types(self, node):
        types = set()

        class HttpResponseVisitor(NodeVisitor):
            def visit_Return(self, node: Return) -> Any:
                nonlocal types

                with suppress(AttributeError):
                    checks = [node.value.func.id]  # type: ignore
                    if node.value.keywords:  # type: ignore
                        checks += [
                            k.value
                            for k in node.value.keywords  # type: ignore
                            if k.arg == "content_type"
                        ]

                    for check in checks:
                        if check in RESPONSE_MAPPING:
                            types.add(RESPONSE_MAPPING[check])

        HttpResponseVisitor().visit(node)

        return types

    def _build_route_context(self, raw: dict[str, Any]) -> HashableDict:
        ctx_kwargs = {
            key.replace("ctx_", ""): raw.pop(key)
            for key in {**raw}.keys()
            if key.startswith("ctx_")
        }
        if raw:
            unexpected_arguments = ", ".join(raw.keys())
            raise TypeError(
                f"Unexpected keyword arguments: {unexpected_arguments}"
            )
        return HashableDict(ctx_kwargs)
