from __future__ import annotations

import asyncio

from collections import defaultdict
from collections.abc import Iterable, Iterator, MutableSequence, Sequence
from copy import deepcopy
from functools import partial, wraps
from inspect import isfunction
from itertools import chain
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    overload,
)

from sanic_routing.exceptions import NotFound
from sanic_routing.route import Route

from sanic.base.root import BaseSanic
from sanic.exceptions import SanicException
from sanic.helpers import Default, _default
from sanic.models.futures import FutureRoute, FutureSignal, FutureStatic
from sanic.models.handler_types import (
    ListenerType,
    MiddlewareType,
    RouteHandler,
)


if TYPE_CHECKING:
    from sanic import Sanic


def lazy(func, as_decorator=True):
    """项目内部接口说明。"""

    @wraps(func)
    def decorator(bp, *args, **kwargs):
        nonlocal as_decorator
        kwargs["apply"] = False
        pass_handler = None

        if args and isfunction(args[0]):
            as_decorator = False

        def wrapper(handler):
            future = func(bp, *args, **kwargs)
            if as_decorator:
                future = future(handler)

            if bp.registered:
                for app in bp.apps:
                    bp.register(app, {})

            return future

        return wrapper if as_decorator else wrapper(pass_handler)

    return decorator


class Blueprint(BaseSanic):
    """项目内部接口说明。"""

    __slots__ = (
        "_apps",
        "_future_commands",
        "_future_routes",
        "_future_statics",
        "_future_middleware",
        "_future_listeners",
        "_future_exceptions",
        "_future_signals",
        "_allow_route_overwrite",
        "copied_from",
        "ctx",
        "exceptions",
        "host",
        "listeners",
        "middlewares",
        "routes",
        "statics",
        "strict_slashes",
        "url_prefix",
        "version",
        "version_prefix",
        "websocket_routes",
    )

    def __init__(
        self,
        name: str,
        url_prefix: str | None = None,
        host: list[str] | str | None = None,
        version: int | str | float | None = None,
        strict_slashes: bool | None = None,
        version_prefix: str = "/v",
    ):
        super().__init__(name=name)
        self.reset()
        self._allow_route_overwrite = False
        self.copied_from = ""
        self.ctx = SimpleNamespace()
        self.host = host
        self.strict_slashes = strict_slashes
        self.url_prefix = (
            url_prefix[:-1]
            if url_prefix and url_prefix.endswith("/")
            else url_prefix
        )
        self.version = version
        self.version_prefix = version_prefix

    def __repr__(self) -> str:
        args = ", ".join(
            [
                f'{attr}="{getattr(self, attr)}"'
                if isinstance(getattr(self, attr), str)
                else f"{attr}={getattr(self, attr)}"
                for attr in (
                    "name",
                    "url_prefix",
                    "host",
                    "version",
                    "strict_slashes",
                )
            ]
        )
        return f"Blueprint({args})"

    @property
    def apps(self) -> set[Sanic]:
        """项目内部接口说明。"""
        if not self._apps:
            raise SanicException(
                f"{self} has not yet been registered to an app"
            )
        return self._apps

    @property
    def registered(self) -> bool:
        """项目内部接口说明。"""
        return bool(self._apps)

    exception = lazy(BaseSanic.exception)
    listener = lazy(BaseSanic.listener)
    middleware = lazy(BaseSanic.middleware)
    route = lazy(BaseSanic.route)
    signal = lazy(BaseSanic.signal)
    static = lazy(BaseSanic.static, as_decorator=False)

    def reset(self) -> None:
        """项目内部接口说明。"""
        self._apps: set[Sanic] = set()
        self._allow_route_overwrite = False
        self.exceptions: list[RouteHandler] = []
        self.listeners: dict[str, list[ListenerType[Any]]] = {}
        self.middlewares: list[MiddlewareType] = []
        self.routes: list[Route] = []
        self.statics: list[RouteHandler] = []
        self.websocket_routes: list[Route] = []

    def copy(
        self,
        name: str,
        url_prefix: str | Default | None = _default,
        version: int | str | float | Default | None = _default,
        version_prefix: str | Default = _default,
        allow_route_overwrite: bool | Default = _default,
        strict_slashes: bool | Default | None = _default,
        with_registration: bool = True,
        with_ctx: bool = False,
    ):
        """项目内部接口说明。"""

        attrs_backup = {
            "_apps": self._apps,
            "routes": self.routes,
            "websocket_routes": self.websocket_routes,
            "middlewares": self.middlewares,
            "exceptions": self.exceptions,
            "listeners": self.listeners,
            "statics": self.statics,
        }

        self.reset()
        new_bp = deepcopy(self)
        new_bp.name = name
        new_bp.copied_from = self.name

        if not isinstance(url_prefix, Default):
            new_bp.url_prefix = url_prefix
        if not isinstance(version, Default):
            new_bp.version = version
        if not isinstance(strict_slashes, Default):
            new_bp.strict_slashes = strict_slashes
        if not isinstance(version_prefix, Default):
            new_bp.version_prefix = version_prefix
        if not isinstance(allow_route_overwrite, Default):
            new_bp._allow_route_overwrite = allow_route_overwrite

        for key, value in attrs_backup.items():
            setattr(self, key, value)

        if with_registration and self._apps:
            if new_bp._future_statics:
                raise SanicException(
                    "Static routes registered with the old blueprint instance,"
                    " cannot be registered again."
                )
            for app in self._apps:
                app.blueprint(new_bp)

        if not with_ctx:
            new_bp.ctx = SimpleNamespace()

        return new_bp

    @staticmethod
    def group(
        *blueprints: Blueprint | BlueprintGroup,
        url_prefix: str | None = None,
        version: int | str | float | None = None,
        strict_slashes: bool | None = None,
        version_prefix: str = "/v",
        name_prefix: str | None = "",
    ) -> BlueprintGroup:
        """项目内部接口说明。"""

        def chain(nested) -> Iterable[Blueprint]:
            """项目内部接口说明。"""
            for i in nested:
                if isinstance(i, (list, tuple)):
                    yield from chain(i)
                else:
                    yield i

        bps = BlueprintGroup(
            url_prefix=url_prefix,
            version=version,
            strict_slashes=strict_slashes,
            version_prefix=version_prefix,
            name_prefix=name_prefix,
        )
        for bp in chain(blueprints):
            bps.append(bp)
        return bps

    def register(self, app, options):
        """项目内部接口说明。"""

        self._apps.add(app)
        url_prefix = options.get("url_prefix", self.url_prefix)
        opt_version = options.get("version", None)
        opt_strict_slashes = options.get("strict_slashes", None)
        opt_version_prefix = options.get("version_prefix", self.version_prefix)
        opt_name_prefix = options.get("name_prefix", None)
        error_format = options.get(
            "error_format", app.config.FALLBACK_ERROR_FORMAT
        )

        routes = []
        middleware = []
        exception_handlers = []
        listeners = defaultdict(list)
        registered = set()

        # Routes
        for future in self._future_routes:
            # Prepend the blueprint URI prefix if available
            uri = self._setup_uri(future.uri, url_prefix)

            route_error_format = (
                future.error_format if future.error_format else error_format
            )

            version_prefix = self.version_prefix
            for prefix in (
                future.version_prefix,
                opt_version_prefix,
            ):
                if prefix and prefix != "/v":
                    version_prefix = prefix
                    break

            version = self._extract_value(
                future.version, opt_version, self.version
            )
            strict_slashes = self._extract_value(
                future.strict_slashes, opt_strict_slashes, self.strict_slashes
            )

            name = future.name
            if opt_name_prefix:
                name = f"{opt_name_prefix}_{future.name}"
            name = app.generate_name(name)
            host = future.host or self.host
            if isinstance(host, list):
                host = tuple(host)

            apply_route = FutureRoute(
                future.handler,
                uri,
                future.methods,
                host,
                strict_slashes,
                future.stream,
                version,
                name,
                future.ignore_body,
                future.websocket,
                future.subprotocols,
                future.unquote,
                future.static,
                version_prefix,
                route_error_format,
                future.route_context,
            )

            if (self, apply_route) in app._future_registry:
                continue

            registered.add(apply_route)
            route = app._apply_route(
                apply_route, overwrite=self._allow_route_overwrite
            )

            # If it is a copied BP, then make sure all of the names of routes
            # matchup with the new BP name
            if self.copied_from:
                for r in route:
                    r.name = r.name.replace(self.copied_from, self.name)
                    r.extra.ident = r.extra.ident.replace(
                        self.copied_from, self.name
                    )

            operation = (
                routes.extend if isinstance(route, list) else routes.append
            )
            operation(route)

        # Static Files
        for future in self._future_statics:
            # Prepend the blueprint URI prefix if available
            uri = self._setup_uri(future.uri, url_prefix)
            apply_route = FutureStatic(uri, *future[1:])

            if (self, apply_route) in app._future_registry:
                continue

            registered.add(apply_route)
            route = app._apply_static(apply_route)
            routes.append(route)

        route_names = [route.name for route in routes if route]

        if route_names:
            # Middleware
            for future in self._future_middleware:
                if (self, future) in app._future_registry:
                    continue
                middleware.append(app._apply_middleware(future, route_names))

            # Exceptions
            for future in self._future_exceptions:
                if (self, future) in app._future_registry:
                    continue
                exception_handlers.append(
                    app._apply_exception_handler(future, route_names)
                )

        # Event listeners
        for future in self._future_listeners:
            if (self, future) in app._future_registry:
                continue
            listeners[future.event].append(app._apply_listener(future))

        # Signals
        for future in self._future_signals:
            if (self, future) in app._future_registry:
                continue
            future.condition.update({"__blueprint__": self.name})
            # Force exclusive to be False
            app._apply_signal(
                FutureSignal(
                    future.handler,
                    future.event,
                    future.condition,
                    False,
                    future.priority,
                )
            )

        self.routes += [route for route in routes if isinstance(route, Route)]
        self.websocket_routes += [
            route for route in self.routes if route.extra.websocket
        ]
        self.middlewares += middleware
        self.exceptions += exception_handlers
        self.listeners.update(dict(listeners))

        if self.registered:
            self.register_futures(
                self.apps,
                self,
                chain(
                    registered,
                    self._future_middleware,
                    self._future_exceptions,
                    self._future_listeners,
                    self._future_signals,
                ),
            )

        if self._future_commands:
            raise SanicException(
                "Registering commands with blueprints is not supported."
            )

    async def dispatch(self, *args, **kwargs):
        """项目内部接口说明。"""
        condition = kwargs.pop("condition", {})
        condition.update({"__blueprint__": self.name})
        kwargs["condition"] = condition
        return await asyncio.gather(
            *[app.dispatch(*args, **kwargs) for app in self.apps]
        )

    def event(
        self,
        event: str,
        timeout: int | float | None = None,
        *,
        condition: dict[str, Any] | None = None,
    ):
        """项目内部接口说明。"""
        if condition is None:
            condition = {}
        condition.update({"__blueprint__": self.name})

        waiters = []
        for app in self.apps:
            waiter = app.signal_router.get_waiter(
                event, condition, exclusive=False
            )
            if not waiter:
                raise NotFound("Could not find signal %s" % event)
            waiters.append(waiter)

        return self._event(waiters, timeout)

    async def _event(self, waiters, timeout):
        done, pending = await asyncio.wait(
            [asyncio.create_task(waiter.wait()) for waiter in waiters],
            return_when=asyncio.FIRST_COMPLETED,
            timeout=timeout,
        )
        for task in pending:
            task.cancel()
        if not done:
            raise TimeoutError()
        (finished_task,) = done
        return finished_task.result()

    @staticmethod
    def _extract_value(*values):
        value = values[-1]
        for v in values:
            if v is not None:
                value = v
                break
        return value

    @staticmethod
    def _setup_uri(base: str, prefix: str | None):
        uri = base
        if prefix:
            uri = prefix
            if base.startswith("/") and prefix.endswith("/"):
                uri += base[1:]
            else:
                uri += base

        return uri[1:] if uri.startswith("//") else uri

    @staticmethod
    def register_futures(
        apps: set[Sanic], bp: Blueprint, futures: Sequence[tuple[Any, ...]]
    ):
        """项目内部接口说明。"""

        for app in apps:
            app._future_registry.update({(bp, item) for item in futures})


bpg_base = MutableSequence[Blueprint]


class BlueprintGroup(bpg_base):
    """项目内部接口说明。"""

    __slots__ = (
        "_blueprints",
        "_url_prefix",
        "_version",
        "_strict_slashes",
        "_version_prefix",
        "_name_prefix",
    )

    def __init__(
        self,
        url_prefix: str | None = None,
        version: int | str | float | None = None,
        strict_slashes: bool | None = None,
        version_prefix: str = "/v",
        name_prefix: str | None = "",
    ):
        self._blueprints: list[Blueprint] = []
        self._url_prefix = url_prefix
        self._version = version
        self._version_prefix = version_prefix
        self._strict_slashes = strict_slashes
        self._name_prefix = name_prefix

    @property
    def url_prefix(self) -> int | str | float | None:
        """项目内部接口说明。"""
        return self._url_prefix

    @property
    def blueprints(self) -> list[Blueprint]:
        """项目内部接口说明。"""
        return self._blueprints

    @property
    def version(self) -> str | int | float | None:
        """项目内部接口说明。"""
        return self._version

    @property
    def strict_slashes(self) -> bool | None:
        """项目内部接口说明。"""
        return self._strict_slashes

    @property
    def version_prefix(self) -> str:
        """项目内部接口说明。"""
        return self._version_prefix

    @property
    def name_prefix(self) -> str | None:
        """项目内部接口说明。"""
        return self._name_prefix

    def __iter__(self) -> Iterator[Blueprint]:
        """项目内部接口说明。"""
        return iter(self._blueprints)

    @overload
    def __getitem__(self, item: int) -> Blueprint: ...

    @overload
    def __getitem__(self, item: slice) -> MutableSequence[Blueprint]: ...

    def __getitem__(
        self, item: int | slice
    ) -> Blueprint | MutableSequence[Blueprint]:
        """项目内部接口说明。"""
        return self._blueprints[item]

    @overload
    def __setitem__(self, index: int, item: Blueprint) -> None: ...

    @overload
    def __setitem__(self, index: slice, item: Iterable[Blueprint]) -> None: ...

    def __setitem__(
        self,
        index: int | slice,
        item: Blueprint | Iterable[Blueprint],
    ) -> None:
        """项目内部接口说明。"""
        if isinstance(index, int):
            if not isinstance(item, Blueprint):
                raise TypeError("Expected a Blueprint instance")
            self._blueprints[index] = item
        elif isinstance(index, slice):
            if not isinstance(item, Iterable):
                raise TypeError("Expected an iterable of Blueprint instances")
            self._blueprints[index] = list(item)
        else:
            raise TypeError("Index must be int or slice")

    @overload
    def __delitem__(self, index: int) -> None: ...

    @overload
    def __delitem__(self, index: slice) -> None: ...

    def __delitem__(self, index: int | slice) -> None:
        """项目内部接口说明。"""
        del self._blueprints[index]

    def __len__(self) -> int:
        """项目内部接口说明。"""
        return len(self._blueprints)

    def append(self, value: Blueprint) -> None:
        """项目内部接口说明。"""
        self._blueprints.append(value)

    def exception(self, *exceptions: Exception, **kwargs) -> Callable:
        """项目内部接口说明。"""

        def register_exception_handler_for_blueprints(fn):
            for blueprint in self.blueprints:
                blueprint.exception(*exceptions, **kwargs)(fn)

        return register_exception_handler_for_blueprints

    def insert(self, index: int, item: Blueprint) -> None:
        """项目内部接口说明。"""
        self._blueprints.insert(index, item)

    def middleware(self, *args, **kwargs):
        """项目内部接口说明。"""

        def register_middleware_for_blueprints(fn):
            for blueprint in self.blueprints:
                blueprint.middleware(fn, *args, **kwargs)

        if args and callable(args[0]):
            fn = args[0]
            args = list(args)[1:]
            return register_middleware_for_blueprints(fn)
        return register_middleware_for_blueprints

    def on_request(self, middleware=None):
        """项目内部接口说明。"""
        if callable(middleware):
            return self.middleware(middleware, "request")
        else:
            return partial(self.middleware, attach_to="request")

    def on_response(self, middleware=None):
        """项目内部接口说明。"""
        if callable(middleware):
            return self.middleware(middleware, "response")
        else:
            return partial(self.middleware, attach_to="response")
