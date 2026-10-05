"""分阶段发布单元（staged rollout）。

在不改动既有应用路由表的前提下，把一组蓝图路由作为一个原子的
"发布单元" 上线：

- ``check``    预检查冲突（路由名 / 蓝图重复），不生效；
- ``publish``  一次性切换，可限定租户范围（内部租户先行）；
- ``promote``  扩大租户范围直至全量；
- ``rollback`` 撤销发布，从引流链摘除，立即释放名称，但保留在途请求
  仍在引用的旧版本；
- ``drain``    等待旧版本在途请求排空，之后版本可被回收。

每个发布单元持有一张**独立的覆盖路由表**（独立的
:class:`~sanic.router.Router` 与
:class:`~sanic.handlers.error.ErrorHandler`）。
请求解析时按租户沿覆盖链从上到下查找，未命中再回落应用基线表，因此：

- 基线路由表在整个生命周期内不被重置 / 重编译，正在处理的请求零影响；
- 切换 / 扩围 / 撤销只是对不可变覆盖链做一次引用交换，请求要么看到
  完整的旧版本，要么看到完整的新版本，不会出现 "只有部分方法可达"；
- 覆盖路由的名称、蓝图中间件、异常处理器都封闭在单元内部：重复发布
  幂等、不产生重复名称；并发撤销只有一个执行者真正摘除版本。

路由 URI 可以与基线相同，但路由的限定名取自蓝图名，因此灰度蓝图必须
使用与基线蓝图不同的名字（即 "同路径、新蓝图名、新 handler"）：

.. code-block:: python

    # 基线
    bp = Blueprint("orders")
    @bp.get("/orders/<oid:int>", name="detail")
    async def old_detail(request, oaid): ...
    app.blueprint(bp)

    # 灰度：新蓝图名承载新版本，URI 保持一致
    canary = Blueprint("orders_canary")
    @canary.get("/orders/<oid:int>", name="detail")
    async def new_detail(request, oid): ...

    unit = app.create_rollout("orders-v2", canary)
    unit.check().publish(tenants=["internal"])

若灰度蓝图与已注册蓝图同名（限定名一致），:meth:`RouteRollout.check`
会报告名称冲突。
"""

from __future__ import annotations

import asyncio

from collections import deque
from contextlib import suppress
from enum import Enum
from operator import attrgetter
from threading import Lock
from typing import TYPE_CHECKING, Callable, Iterable

from sanic_routing.exceptions import (
    FinalizationError,
    NoMethod,
    RouteExists,
)
from sanic_routing.exceptions import (
    NotFound as RoutingNotFound,
)

from sanic.blueprints import Blueprint
from sanic.exceptions import MethodNotAllowed, SanicException
from sanic.handlers.error import ErrorHandler
from sanic.middleware import Middleware, MiddlewareLocation
from sanic.models.futures import FutureRoute
from sanic.router import Router


if TYPE_CHECKING:
    from sanic_routing.route import Route

    from sanic.app import Sanic
    from sanic.request import Request


class RolloutStatus(str, Enum):
    """发布单元生命周期状态。"""

    STAGED = "staged"
    PUBLISHED = "published"
    ROLLED_BACK = "rolled_back"


class RolloutConflict(SanicException):
    """预检查发现冲突（路由名 / 蓝图重复注册）。"""

    status_code = 409


class RolloutError(SanicException):
    """发布单元操作不满足前置条件。"""

    status_code = 400


# 解析请求时用于判定租户归属的可调用对象。
TenantResolver = Callable[["Request"], str | None]


class RolloutErrorHandler(ErrorHandler):
    """发布单元私有的异常处理器。

    蓝图注册的异常处理器按覆盖路由名在此命中；未命中时回落到应用的全局
    异常处理器，从而既跟随请求绑定的版本，又不丢失应用级兜底。
    """

    def __init__(self, fallback: ErrorHandler) -> None:
        super().__init__()
        self._fallback = fallback

    def lookup(self, exception, route_name: str | None = None):
        exception_class = type(exception)

        # 先在单元自身的（蓝图）处理器中精确匹配。
        for name in (route_name, None):
            handler = self.cached_handlers.get((exception_class, name))
            if handler:
                return handler

        # 再沿异常 MRO 匹配单元自身处理器；不缓存 None，以免遮蔽回落。
        for name in (route_name, None):
            for ancestor in type.mro(exception_class):
                handler = self.cached_handlers.get((ancestor, name))
                if handler:
                    return handler
                if ancestor is BaseException:
                    break

        return self._fallback.lookup(exception, route_name)


def _full_name(app: "Sanic", name: str) -> str:
    """把视图名补全为 ``app.name`` 限定的全名。"""
    if "." not in name:
        return f"{app.name}.{name}"
    return name


def _method_index(router: Router) -> dict[tuple, frozenset[str]]:
    """汇总 ``(路径分段, host 要求) -> 方法集合``，用于方法覆盖预检。"""
    index: dict[tuple, set[str]] = {}
    for route in router.routes:
        host = route.requirements.get("host") if route.requirements else None
        key = (route.parts, host)
        index.setdefault(key, set()).update(route.methods)
    return {key: frozenset(methods) for key, methods in index.items()}


class RouteRollout:
    """一组蓝图路由的分阶段发布单元。"""

    def __init__(
        self,
        app: "Sanic",
        name: str,
        *blueprints: Blueprint,
        tenant_header: str | None = None,
        strict_methods: bool = True,
    ) -> None:
        if not blueprints:
            raise RolloutError("A rollout must contain at least one blueprint")
        if not name or "." in name:
            raise RolloutError(
                "Rollout name must be a non-empty string without '.'"
            )
        self.app = app
        self.name = name
        self.blueprints = tuple(blueprints)
        self.tenant_header = tenant_header
        # True（默认）时，预检查要求新版本在每条重合路径上的方法集合与
        # 现状一致，防止切换后出现 "只有部分方法可达"；确需增删方法时
        # 显式置 False。
        self.strict_methods = strict_methods
        self.status = RolloutStatus.STAGED
        self.generation: int | None = None

        # 引流范围：_all_tenants=True 表示全量；否则取 _tenants。
        self._tenants: frozenset[str] = frozenset()
        self._all_tenants = False

        # 在途请求与排空等待者。直接持有请求对象：撤销后旧版本仍可等待
        # 这些请求结束，且不会因 id() 复用而误删新请求。
        self._in_flight: set["Request"] = set()
        self._waiters: list[asyncio.Future] = []

        self._names: tuple[str, ...] = ()
        self._router: Router = self._new_router()
        self._error_handler = RolloutErrorHandler(app.error_handler)

        # 构建并一次性验证覆盖表；构造期冲突直接抛出，publish 时不再
        # 存在可能半途失败的结构性变更。
        self._build()

    # ------------------------------------------------------------------ #
    # 构建覆盖表
    # ------------------------------------------------------------------ #
    def _new_router(self) -> Router:
        router = Router()
        router.ctx.app = self.app
        return router

    def _route_futures_for(self, bp: Blueprint) -> Iterable[FutureRoute]:
        """复刻 Blueprint.register 对 FutureRoute 的解析，但不写入应用。"""
        for future in bp._future_routes:
            uri = bp._setup_uri(future.uri, bp.url_prefix)

            version_prefix = bp.version_prefix
            if future.version_prefix and future.version_prefix != "/v":
                version_prefix = future.version_prefix

            version = bp._extract_value(future.version, bp.version)
            strict_slashes = bp._extract_value(
                future.strict_slashes, bp.strict_slashes
            )
            name = self.app.generate_name(future.name)
            host = future.host or bp.host
            if isinstance(host, list):
                host = tuple(host)

            yield FutureRoute(
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
                future.error_format,
                future.route_context,
            )

    @staticmethod
    def _as_router_params(future: FutureRoute) -> dict:
        params = future._asdict()
        # 这三项由 app 层在落表前弹出 / 另行处理。
        params.pop("websocket", None)
        params.pop("subprotocols", None)
        params.pop("route_context", None)
        params["overwrite"] = False
        return params

    def _build(self) -> None:
        names: list[str] = []
        seen_bp: set[str] = set()
        added_routes: list[Route] = []

        for bp in self.blueprints:
            if bp.name in seen_bp:
                raise RolloutConflict(
                    f"Blueprint {bp.name!r} is included more than once in "
                    f"rollout {self.name!r}"
                )
            seen_bp.add(bp.name)

            bp_req: list[Middleware] = []
            bp_resp: list[Middleware] = []
            for fm in bp._future_middleware:
                mw = (
                    fm.middleware
                    if isinstance(fm.middleware, Middleware)
                    else Middleware(
                        fm.middleware,
                        location=MiddlewareLocation[fm.attach_to.upper()],
                    )
                )
                if fm.attach_to == "request":
                    bp_req.append(mw)
                else:
                    bp_resp.append(mw)
            req_tuple = tuple(bp_req)
            resp_tuple = tuple(bp_resp)
            req_deque = deque(
                sorted(bp_req, key=attrgetter("order"), reverse=True)
            )
            resp_deque = deque(
                sorted(bp_resp, key=attrgetter("order"), reverse=True)[::-1]
            )

            bp_routes: list[Route] = []
            for future in self._route_futures_for(bp):
                if future.websocket:
                    raise RolloutError(
                        "Staged rollouts do not support websocket routes"
                    )

                full = _full_name(self.app, future.name)
                if full in names:
                    raise RolloutConflict(
                        f"Route name {full!r} is duplicated inside rollout "
                        f"{self.name!r}"
                    )

                try:
                    routes = self._router.add(**self._as_router_params(future))
                except RouteExists as e:
                    raise RolloutConflict(
                        f"Conflicting routes inside rollout {self.name!r}: {e}"
                    ) from e
                if not isinstance(routes, list):
                    routes = [routes]

                for route in routes:
                    # Router.add 已写入大部分 extra；这里补齐并打版本标记。
                    route.extra.ident = full
                    route.extra.rollout = self.name
                    # 强引用回发布单元：只要还有在途请求（或调用方持有
                    # unit），旧版本的路由 / 中间件 / 异常处理器就不回收。
                    route.extra.rollout_unit = self
                    route.ctx.__dict__.update(dict(future.route_context))
                    # 只绑定本蓝图的命名中间件；与全局中间件的合并在请求
                    # 期（merged_middleware）完成，因全局中间件可能晚注册。
                    route.extra._rollout_req = req_tuple
                    route.extra._rollout_resp = resp_tuple
                    route.extra.request_middleware = req_deque
                    route.extra.response_middleware = resp_deque
                    added_routes.append(route)
                    bp_routes.append(route)

                names.append(full)

            # 蓝图异常处理器注册到单元私有 ErrorHandler，按本蓝图路由名
            # 限定；多主机注册会产生同名路由，需去重，否则 ErrorHandler
            # 会把同一 (异常, 路由名) 视为重复注册。
            bp_names = list(dict.fromkeys(r.name for r in bp_routes))
            for fe in bp._future_exceptions:
                for exception in fe.exceptions:
                    excs = (
                        exception
                        if isinstance(exception, (tuple, list))
                        else [exception]
                    )
                    for exc in excs:
                        self._error_handler.add(exc, fe.handler, bp_names)

        try:
            self._router.finalize()
        except FinalizationError as e:  # pragma: no cover - 空表不会发生
            raise RolloutConflict(str(e)) from e

        self._method_index = _method_index(self._router)
        self._names = tuple(names)

    # ------------------------------------------------------------------ #
    # 预检查
    # ------------------------------------------------------------------ #
    @property
    def names(self) -> tuple[str, ...]:
        """单元内路由的全名。"""
        return self._names

    @property
    def error_handler(self) -> ErrorHandler:
        """单元私有的异常处理器（含蓝图异常处理）。"""
        return self._error_handler

    def check(self) -> "RouteRollout":
        """预检查与基线表及已发布单元的冲突，不改变任何状态。"""
        with self.app.rollouts._lock:
            self._check_locked()
        return self

    def _check_locked(self) -> None:
        manager = self.app.rollouts
        existing_names = {
            _full_name(self.app, name) for name in self.app.router.name_index
        }
        for rollout in manager._units.values():
            if rollout is self:
                continue
            existing_names.update(rollout._names)
            overlap = {bp.name for bp in self.blueprints} & {
                bp.name for bp in rollout.blueprints
            }
            if overlap:
                raise RolloutConflict(
                    f"Blueprint(s) {sorted(overlap)} already published by "
                    f"rollout {rollout.name!r}"
                )

        duplicated = sorted({n for n in self._names if n in existing_names})
        if duplicated:
            raise RolloutConflict(
                "Route name(s) already registered: " + ", ".join(duplicated)
            )

        if self.strict_methods:
            # 方法覆盖完整性：新版本在每条与现状重合的路径上必须提供完全
            # 相同的方法集合，否则切换后该路径会出现部分方法可达 / 405。
            self._check_methods_locked(manager)

    def _check_methods_locked(self, manager: "RolloutManager") -> None:
        existing_index = _method_index(self.app.router)
        for rollout in manager._units.values():
            if rollout is not self:
                existing_index.update(rollout._method_index)

        for key, new_methods in self._method_index.items():
            old_methods = existing_index.get(key)
            if old_methods is None:
                continue
            if new_methods != old_methods:
                parts, host = key
                path = "/" + "/".join(parts)
                if host:
                    path = f"{path} (host={host})"
                missing = sorted(old_methods - new_methods)
                extra = sorted(new_methods - old_methods)
                details = []
                if missing:
                    details.append(f"missing {missing}")
                if extra:
                    details.append(f"unexpected {extra}")
                raise RolloutConflict(
                    f"Method coverage mismatch on {path}: "
                    + ", ".join(details)
                    + ". Pass strict_methods=False to allow adding or "
                    "removing methods in a staged rollout."
                )

    # ------------------------------------------------------------------ #
    # 状态流转（一次性、原子、幂等）
    # ------------------------------------------------------------------ #
    def publish(
        self,
        tenants: Iterable[str] | None = None,
        *,
        all_tenants: bool = False,
    ) -> "RouteRollout":
        """一次性切换上线。

        :param tenants: 允许命中本单元的租户标识集合。
        :param all_tenants: 为 ``True`` 时直接全量；否则仅 ``tenants``
            可达。两者都不给时单元已注册但暂不引流，可随后
            :meth:`promote`（先就位、再切换）。
        """
        manager = self.app.rollouts
        with manager._lock:
            if self.status is RolloutStatus.PUBLISHED:
                # 幂等：重复发布不重复注册、不产生重复名称，仅更新范围。
                self._apply_scope(tenants, all_tenants)
                manager._rebuild_chain_locked()
                return self
            if self.status is RolloutStatus.ROLLED_BACK:
                raise RolloutError(
                    f"Rollout {self.name!r} has been rolled back; create a "
                    "new rollout to republish"
                )

            # 持锁做最终冲突预检，堵住 check→publish 之间的竞态。
            self._check_locked()
            if self.name in manager._units:
                raise RolloutConflict(
                    f"Rollout name {self.name!r} is already in use"
                )

            self._apply_scope(tenants, all_tenants)
            self.generation = manager._next_generation()
            self.status = RolloutStatus.PUBLISHED
            manager._units[self.name] = self
            manager._rebuild_chain_locked()
        return self

    def promote(
        self,
        tenants: Iterable[str] | None = None,
        *,
        all_tenants: bool = False,
    ) -> "RouteRollout":
        """扩大租户范围（可叠加），或切换为全量。"""
        manager = self.app.rollouts
        with manager._lock:
            if self.status is not RolloutStatus.PUBLISHED:
                raise RolloutError(
                    f"Rollout {self.name!r} must be published before promotion"
                )
            if all_tenants:
                self._tenants = frozenset()
                self._all_tenants = True
            else:
                merged = set(self._tenants)
                if tenants:
                    merged.update(tenants)
                self._tenants = frozenset(merged)
            manager._rebuild_chain_locked()
        return self

    def rollback(self) -> "RouteRollout":
        """撤销发布。幂等：重复 / 并发撤销仅一个执行者真正摘除版本。"""
        manager = self.app.rollouts
        with manager._lock:
            if self.status is not RolloutStatus.PUBLISHED:
                # 第二个（并发或重复）撤销者看到稳定结果，不再触碰路由表，
                # 也不会释放两次名称 / 版本。
                return self
            self.status = RolloutStatus.ROLLED_BACK
            manager._units.pop(self.name, None)
            manager._rebuild_chain_locked()
            # 名称立即释放，可被新单元复用；在途请求仍通过
            # route.extra.rollout_unit 持有旧版本，直到排空。
        return self

    def _apply_scope(
        self,
        tenants: Iterable[str] | None,
        all_tenants: bool,
    ) -> None:
        if all_tenants:
            self._tenants = frozenset()
            self._all_tenants = True
        elif tenants is None:
            self._tenants = frozenset()
            self._all_tenants = False
        else:
            self._tenants = frozenset(tenants)
            self._all_tenants = False

    # ------------------------------------------------------------------ #
    # 请求解析
    # ------------------------------------------------------------------ #
    def allows(self, tenant: str | None) -> bool:
        """该租户当前是否命中本单元。"""
        if self._all_tenants:
            return True
        return bool(tenant) and tenant in self._tenants

    def resolve(self, path: str, method: str, host: str | None):
        """在覆盖表中解析。

        :return: 命中返回 ``(route, handler, kwargs)``；路径不存在返回
            ``None``（调用方回落基线表）；路径存在但方法不允许则抛出
            :class:`~sanic.exceptions.MethodNotAllowed`。
        """
        try:
            return self._router.resolve(
                path=path,
                method=method,
                extra={"host": host} if host else None,
            )
        except RoutingNotFound:
            return None
        except NoMethod as e:
            raise MethodNotAllowed(
                f"Method {method} not allowed for URL {path}",
                method=method,
                allowed_methods=tuple(e.allowed_methods)
                if e.allowed_methods
                else None,
            ) from None

    def find_route_by_view_name(self, view_name: str):
        """在本单元覆盖表内做反向名称查找。"""
        return self._router.find_route_by_view_name(view_name)

    def merged_middleware(self, app: "Sanic", route: "Route"):
        """合并该路由的蓝图中间件与应用全局中间件，排序同
        ``finalize_middleware``。按当前全局中间件容器缓存，应用在运行期
        增删中间件会触发 :meth:`Sanic.amend` 重建容器，缓存随之失效。
        """
        key = (
            id(app.request_middleware),
            len(app.request_middleware),
            id(app.response_middleware),
            len(app.response_middleware),
        )
        cache: dict | None = getattr(self, "_mw_cache", None)
        if cache is not None and cache.get("key") == key:
            entry = cache.get(id(route))
            if entry is not None:
                return entry

        bp_req = tuple(getattr(route.extra, "_rollout_req", ()))
        bp_resp = tuple(getattr(route.extra, "_rollout_resp", ()))
        # 全局容器里可能混有尚未包成 Middleware 的裸函数（如测试 / 扩展
        # 注入），用框架自身的 convert 归一化，与 finalize_middleware 一致。
        global_req = Middleware.convert(
            app.request_middleware, location=MiddlewareLocation.REQUEST
        )
        global_resp = Middleware.convert(
            app.response_middleware, location=MiddlewareLocation.RESPONSE
        )
        req = deque(
            sorted(
                tuple(global_req) + bp_req,
                key=attrgetter("order"),
                reverse=True,
            )
        )
        resp = deque(
            sorted(
                tuple(global_resp) + bp_resp,
                key=attrgetter("order"),
                reverse=True,
            )[::-1]
        )
        if cache is None or cache.get("key") != key:
            cache = {"key": key}
            self._mw_cache = cache
        cache[id(route)] = (req, resp)
        return req, resp

    # ------------------------------------------------------------------ #
    # 在途计数与排空
    # ------------------------------------------------------------------ #
    def enter(self, token: "Request") -> None:
        self._in_flight.add(token)

    def leave(self, token: "Request") -> None:
        self._in_flight.discard(token)
        if not self._in_flight and self._waiters:
            waiters, self._waiters = self._waiters, []
            for future in waiters:
                if not future.done():
                    future.set_result(None)

    @property
    def in_flight(self) -> int:
        """仍在处理中、绑定到本单元的请求数。"""
        return len(self._in_flight)

    async def drain(self, timeout: float | None = None) -> int:
        """等待绑定到本单元的在途请求排空。

        :param timeout: 最长等待秒数；``None`` 表示一直等待。
        :return: 等待结束后剩余的在途请求数（超时情况下可能非 0）。
        """
        if not self._in_flight:
            return 0
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._waiters.append(future)
        try:
            if timeout is None:
                await future
            else:
                await asyncio.wait_for(asyncio.shield(future), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        finally:
            with suppress(ValueError):
                self._waiters.remove(future)
        return len(self._in_flight)

    def __repr__(self) -> str:
        scope = "ALL" if self._all_tenants else sorted(self._tenants)
        return (
            f"<RouteRollout name={self.name!r} "
            f"generation={self.generation} status={self.status.value} "
            f"tenants={scope} routes={len(self._names)}>"
        )


class RolloutManager:
    """挂在 :class:`Sanic` 上的发布单元注册表、覆盖链与租户解析器。"""

    def __init__(self, app: "Sanic") -> None:
        self.app = app
        self._units: dict[str, RouteRollout] = {}
        # 同步锁：状态流转临界区内不 await，单事件循环内本就原子，
        # threading.Lock 同时覆盖跨线程（多 worker / 线程服务器）场景。
        self._lock = Lock()
        # (rollout, scope) 有序链；scope 为 None 表示全量。
        self._chain: tuple[
            tuple[RouteRollout, frozenset[str] | None], ...
        ] = ()
        self._tenant_resolver: TenantResolver | None = None
        self._generation = 0

    # ------------------------------------------------------------------ #
    # 多进程派生（如 multiprocessing worker）需要 pickle app；锁与覆盖链
    # 中的运行期对象不跨进程，序列化时丢弃，反序列化时重建。
    # ------------------------------------------------------------------ #
    def __getstate__(self) -> dict:
        state = self.__dict__.copy()
        state["_lock"] = None
        return state

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        self._lock = Lock()

    def values(self):
        return self._units.values()

    def get(self, name: str) -> RouteRollout | None:
        return self._units.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._units

    def __len__(self) -> int:
        return len(self._units)

    def _next_generation(self) -> int:
        self._generation += 1
        return self._generation

    # ------------------------------------------------------------------ #
    def tenant_resolver(self, func: TenantResolver) -> TenantResolver:
        """注册租户解析器：从请求中取出租户标识。"""
        self._tenant_resolver = func
        return func

    def resolve_tenant(self, request: "Request") -> str | None:
        if self._tenant_resolver is not None:
            return self._tenant_resolver(request)
        header = "x-tenant-id"
        for unit in self._units.values():
            if unit.tenant_header:
                header = unit.tenant_header
                break
        return request.headers.get(header) or None

    # ------------------------------------------------------------------ #
    def _rebuild_chain_locked(self) -> None:
        chain: list[tuple[RouteRollout, frozenset[str] | None]] = []
        for unit in self._units.values():
            scope = None if unit._all_tenants else unit._tenants
            chain.append((unit, scope))
        # 后发布者优先级最高。
        self._chain = tuple(reversed(chain))

    def resolve(
        self,
        request: "Request",
        path: str,
        method: str,
        host: str | None,
    ):
        """按租户沿覆盖链解析；全部未命中返回 ``None`` 回落基线表。"""
        chain = self._chain
        if not chain:
            return None
        tenant = self.resolve_tenant(request)
        for unit, scope in chain:
            if scope is not None and (not tenant or tenant not in scope):
                continue
            result = unit.resolve(path, method, host)
            if result is not None:
                return result, unit
        return None

    # ------------------------------------------------------------------ #
    # 在途计数 / 排空（委托到具体单元）
    # ------------------------------------------------------------------ #
    def enter(self, unit: RouteRollout, token: "Request") -> None:
        unit.enter(token)

    def leave(self, unit: RouteRollout, token: "Request") -> None:
        unit.leave(token)
