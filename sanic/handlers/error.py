from __future__ import annotations

from sanic.errorpages import BaseRenderer, TextRenderer, exception_response
from sanic.exceptions import ServerError
from sanic.log import error_logger
from sanic.models.handler_types import RouteHandler
from sanic.request.types import Request
from sanic.response import text
from sanic.response.types import HTTPResponse


class ErrorHandler:
    """项目内部接口说明。"""

    def __init__(
        self,
        base: type[BaseRenderer] = TextRenderer,
    ):
        self.cached_handlers: dict[
            tuple[type[BaseException], str | None], RouteHandler | None
        ] = {}
        self.debug = False
        self.base = base

    def _full_lookup(self, exception, route_name: str | None = None):
        return self.lookup(exception, route_name)

    def _add(
        self,
        key: tuple[type[BaseException], str | None],
        handler: RouteHandler,
    ) -> None:
        if key in self.cached_handlers:
            exc, name = key
            if name is None:
                name = "__ALL_ROUTES__"

            message = (
                f"Duplicate exception handler definition on: route={name} "
                f"and exception={exc}"
            )
            raise ServerError(message)
        self.cached_handlers[key] = handler

    def add(self, exception, handler, route_names: list[str] | None = None):
        """项目内部接口说明。"""
        if route_names:
            for route in route_names:
                self._add((exception, route), handler)
        else:
            self._add((exception, None), handler)

    def lookup(self, exception, route_name: str | None = None):
        """项目内部接口说明。"""
        exception_class = type(exception)

        for name in (route_name, None):
            exception_key = (exception_class, name)
            handler = self.cached_handlers.get(exception_key)
            if handler:
                return handler

        for name in (route_name, None):
            for ancestor in type.mro(exception_class):
                exception_key = (ancestor, name)
                if exception_key in self.cached_handlers:
                    handler = self.cached_handlers[exception_key]
                    self.cached_handlers[(exception_class, route_name)] = (
                        handler
                    )
                    return handler

                if ancestor is BaseException:
                    break
        self.cached_handlers[(exception_class, route_name)] = None
        handler = None
        return handler

    _lookup = _full_lookup

    def response(self, request, exception):
        """项目内部接口说明。"""
        route_name = request.name if request else None
        handler = self._lookup(exception, route_name)
        response = None
        try:
            if handler:
                response = handler(request, exception)
            if response is None:
                response = self.default(request, exception)
        except Exception:
            try:
                url = repr(request.url)
            except AttributeError:  # no cov
                url = "unknown"
            response_message = (
                'Exception raised in exception handler "%s" for uri: %s'
            )
            error_logger.exception(response_message, handler.__name__, url)

            if self.debug:
                return text(response_message % (handler.__name__, url), 500)
            else:
                return text("An error occurred while handling an error", 500)
        return response

    def default(self, request: Request, exception: Exception) -> HTTPResponse:
        """项目内部接口说明。"""
        self.log(request, exception)
        fallback = request.app.config.FALLBACK_ERROR_FORMAT
        return exception_response(
            request,
            exception,
            debug=self.debug,
            base=self.base,
            fallback=fallback,
        )

    @staticmethod
    def log(request: Request, exception: Exception) -> None:
        """项目内部接口说明。"""
        quiet = getattr(exception, "quiet", False)
        noisy = getattr(request.app.config, "NOISY_EXCEPTIONS", False)
        if quiet is False or noisy is True:
            try:
                url = repr(request.url)
            except AttributeError:  # no cov
                url = "unknown"

            error_logger.exception(
                "Exception occurred while handling uri: %s", url
            )
