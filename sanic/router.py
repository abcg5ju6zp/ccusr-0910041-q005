from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from inspect import signature
from typing import Any
from uuid import UUID

from sanic_routing import BaseRouter
from sanic_routing.exceptions import NoMethod
from sanic_routing.exceptions import NotFound as RoutingNotFound
from sanic_routing.group import RouteGroup
from sanic_routing.route import Route

from sanic.constants import HTTP_METHODS
from sanic.errorpages import check_error_format
from sanic.exceptions import MethodNotAllowed, NotFound, SanicException
from sanic.models.handler_types import RouteHandler


ROUTER_CACHE_SIZE = 1024
ALLOWED_LABELS = ("__file_uri__",)


class Router(BaseRouter):
    """项目内部接口说明。"""

    DEFAULT_METHOD = "GET"
    ALLOWED_METHODS = HTTP_METHODS

    def _get(
        self, path: str, method: str, host: str | None
    ) -> tuple[Route, RouteHandler, dict[str, Any]]:
        try:
            return self.resolve(
                path=path,
                method=method,
                extra={"host": host} if host else None,
            )
        except RoutingNotFound as e:
            raise NotFound(f"Requested URL {e.path} not found") from None
        except NoMethod as e:
            raise MethodNotAllowed(
                f"Method {method} not allowed for URL {path}",
                method=method,
                allowed_methods=tuple(e.allowed_methods)
                if e.allowed_methods
                else None,
            ) from None

    @lru_cache(maxsize=ROUTER_CACHE_SIZE)
    def get(  # type: ignore
        self, path: str, method: str, host: str | None
    ) -> tuple[Route, RouteHandler, dict[str, Any]]:
        """项目内部接口说明。"""
        __tracebackhide__ = True
        return self._get(path, method, host)

    def add(  # type: ignore
        self,
        uri: str,
        methods: Iterable[str],
        handler: RouteHandler,
        host: str | Iterable[str] | None = None,
        strict_slashes: bool = False,
        stream: bool = False,
        ignore_body: bool = False,
        version: str | float | int | None = None,
        name: str | None = None,
        unquote: bool = False,
        static: bool = False,
        version_prefix: str = "/v",
        overwrite: bool = False,
        error_format: str | None = None,
    ) -> Route | list[Route]:
        """项目内部接口说明。"""

        if version is not None:
            version = str(version).strip("/").lstrip("v")
            uri = "/".join([f"{version_prefix}{version}", uri.lstrip("/")])

        uri = self._normalize(uri, handler)

        params = dict(
            path=uri,
            handler=handler,
            methods=frozenset(map(str, methods)) if methods else None,
            name=name,
            strict=strict_slashes,
            unquote=unquote,
            overwrite=overwrite,
        )

        if isinstance(host, str):
            hosts = [host]
        else:
            hosts = host or [None]  # type: ignore

        routes = []

        for host in hosts:
            if host:
                params.update({"requirements": {"host": host}})

            ident = name
            if len(hosts) > 1:
                ident = (
                    f"{name}_{host.replace('.', '_')}"
                    if name
                    else "__unnamed__"
                )

            route = super().add(**params)  # type: ignore
            route.extra.ident = ident
            route.extra.ignore_body = ignore_body
            route.extra.stream = stream
            route.extra.hosts = hosts
            route.extra.static = static
            route.extra.error_format = error_format

            if error_format:
                check_error_format(route.extra.error_format)

            routes.append(route)

        if len(routes) == 1:
            return routes[0]
        return routes

    @lru_cache(maxsize=ROUTER_CACHE_SIZE)
    def find_route_by_view_name(
        self, view_name: str, name: str | None = None
    ) -> Route | None:
        """项目内部接口说明。"""
        if not view_name:
            return None

        route = self.name_index.get(view_name)
        if not route:
            full_name = self.ctx.app.generate_name(view_name)
            route = self.name_index.get(full_name)

        if not route:
            return None

        return route

    @property
    def routes_all(self) -> dict[tuple[str, ...], Route]:
        """项目内部接口说明。"""
        return {route.parts: route for route in self.routes}

    @property
    def routes_static(self) -> dict[tuple[str, ...], RouteGroup]:
        """项目内部接口说明。"""
        return self.static_routes

    @property
    def routes_dynamic(self) -> dict[tuple[str, ...], RouteGroup]:
        """项目内部接口说明。"""
        return self.dynamic_routes

    @property
    def routes_regex(self) -> dict[tuple[str, ...], RouteGroup]:
        """项目内部接口说明。"""
        return self.regex_routes

    def finalize(self, *args, **kwargs) -> None:
        """项目内部接口说明。"""
        super().finalize(*args, **kwargs)

        for route in self.dynamic_routes.values():
            if any(
                label.startswith("__") and label not in ALLOWED_LABELS
                for label in route.labels
            ):
                raise SanicException(
                    f"Invalid route: {route}. Parameter names cannot use '__'."
                )

    def _normalize(self, uri: str, handler: RouteHandler) -> str:
        if "<" not in uri:
            return uri

        sig = signature(handler)
        mapping = {
            param.name: param.annotation.__name__.lower()
            for param in sig.parameters.values()
            if param.annotation in (str, int, float, UUID)
        }

        reconstruction = []
        for part in uri.split("/"):
            if part.startswith("<") and ":" not in part:
                name = part[1:-1]
                annotation = mapping.get(name)
                if annotation:
                    part = f"<{name}:{annotation}>"
            reconstruction.append(part)
        return "/".join(reconstruction)
