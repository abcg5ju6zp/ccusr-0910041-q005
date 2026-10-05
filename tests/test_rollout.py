"""分阶段发布单元（staged rollout）回归测试。

覆盖：预检查冲突、租户范围、一次性切换、撤销、旧版本排空，以及
URL 反向生成 / 异常处理 / 中间件对请求所绑定路由版本的跟随；并验证
重复发布幂等、并发撤销不会制造重复名称或不可回收的版本。
"""

from __future__ import annotations

import asyncio

import pytest

from sanic import Blueprint, Forbidden, json, text
from sanic.rollout import (
    RolloutConflict,
    RolloutError,
    RolloutStatus,
    RouteRollout,
)


HEADERS = {"x-tenant-id": "acme"}


def _canary_bp(name="canary", value="canary"):
    bp = Blueprint(name)

    @bp.get("/items", name="list_items")
    def list_items(request):
        return json({"version": value, "url": app_url_for(request, name)})

    return bp


def app_url_for(request, bp_name):
    # 在处理函数内部反向生成，确保跟随请求绑定的版本。
    return request.app.url_for(f"{bp_name}.list_items")


# --------------------------------------------------------------------- #
# 预检查
# --------------------------------------------------------------------- #
def test_check_detects_duplicate_route_name(app):
    @app.get("/items", name="dup")
    def base(request):
        return text("base")

    # 灰度蓝图与应用同名时，其路由限定名与基线路由完全一致，应被识别为
    # 名称冲突（常见于误把已注册蓝图直接塞进发布单元）。
    bp = Blueprint(app.name)

    @bp.get("/items", name="dup")
    def new(request):
        return text("new")

    with pytest.raises(RolloutConflict) as exc:
        RouteRollout(app, "r1", bp).check()
    assert "already registered" in str(exc.value)
    # check 不改变任何状态：未发布，请求仍走基线。
    _, response = app.test_client.get("/items")
    assert response.text == "base"


def test_check_detects_same_blueprint_in_two_rollouts(app):
    @app.get("/base", name="base")
    def base(request):
        return text("base")

    bp = _canary_bp()
    other = Blueprint("other")

    @other.get("/other", name="other")
    def o(request):
        return text("o")

    RouteRollout(app, "r1", bp).check().publish(tenants=["acme"])

    bp2 = Blueprint("bundle")

    @bp2.get("/bundle", name="bundle")
    def b(request):
        return text("b")

    with pytest.raises(RolloutConflict) as exc:
        RouteRollout(app, "r2", bp, bp2).check()
    assert "already published" in str(exc.value)
    # 冲突时第二个单元不得残留注册。
    assert "r2" not in app.rollouts


def test_check_detects_partial_method_coverage(app):
    @app.get("/multi", name="multi")
    def g(request):
        return text("g")

    @app.post("/multi")
    def p(request):
        return text("p")

    bp = Blueprint("canary")

    # 新版本只实现了 GET，漏了基线已有的 POST。
    @bp.get("/multi", name="multi")
    def ng(request):
        return text("ng")

    with pytest.raises(RolloutConflict) as exc:
        RouteRollout(app, "r1", bp).check()
    assert "Method coverage mismatch" in str(exc.value)
    assert "POST" in str(exc.value)


def test_check_allows_method_change_when_not_strict(app):
    @app.get("/multi", name="multi")
    def g(request):
        return text("g")

    @app.post("/multi")
    def p(request):
        return text("p")

    bp = Blueprint("canary")

    @bp.get("/multi", name="multi")
    def ng(request):
        return text("ng")

    unit = RouteRollout(app, "r1", bp, strict_methods=False)
    unit.check().publish(tenants=["acme"])  # 不抛异常
    _, response = app.test_client.get("/multi", headers=HEADERS)
    assert response.text == "ng"


def test_duplicate_blueprint_inside_one_rollout_rejected(app):
    bp = Blueprint("solo")

    @bp.get("/a", name="a")
    def a(request):
        return text("a")

    with pytest.raises(RolloutConflict):
        RouteRollout(app, "r1", bp, bp)


# --------------------------------------------------------------------- #
# 租户范围与一次性切换
# --------------------------------------------------------------------- #
def test_tenant_scoped_routing(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    bp = _canary_bp()
    RouteRollout(app, "items-v2", bp).check().publish(tenants=["acme"])

    _, internal = app.test_client.get("/items", headers=HEADERS)
    assert internal.json["version"] == "canary"

    _, external = app.test_client.get(
        "/items", headers={"x-tenant-id": "globex"}
    )
    assert external.json["version"] == "base"

    _, anonymous = app.test_client.get("/items")
    assert anonymous.json["version"] == "base"


def test_custom_tenant_resolver(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    bp = _canary_bp()
    RouteRollout(app, "items-v2", bp).check().publish(tenants=["t-7"])

    @app.rollouts.tenant_resolver
    def tenant(request):
        return "t-7" if request.headers.get("x-org") == "7" else None

    _, ok = app.test_client.get("/items", headers={"x-org": "7"})
    assert ok.json["version"] == "canary"
    _, no = app.test_client.get("/items", headers={"x-org": "8"})
    assert no.json["version"] == "base"


def test_publish_without_scope_does_not_route(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    bp = _canary_bp()
    unit = app.create_rollout("r1", bp).check().publish()

    assert unit.status is RolloutStatus.PUBLISHED
    # 已发布但未开放任何租户：所有人仍走基线，可随后 promote 切换。
    _, response = app.test_client.get("/items", headers=HEADERS)
    assert response.json["version"] == "base"

    unit.promote(tenants=["acme"])
    _, response = app.test_client.get("/items", headers=HEADERS)
    assert response.json["version"] == "canary"


def test_promote_expands_and_goes_all(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    bp = _canary_bp()
    unit = RouteRollout(app, "r1", bp).check().publish(tenants=["acme"])

    unit.promote(tenants=["globex"])
    _, g = app.test_client.get("/items", headers={"x-tenant-id": "globex"})
    assert g.json["version"] == "canary"
    _, other = app.test_client.get(
        "/items", headers={"x-tenant-id": "initech"}
    )
    assert other.json["version"] == "base"

    unit.promote(all_tenants=True)
    _, everyone = app.test_client.get(
        "/items", headers={"x-tenant-id": "initech"}
    )
    assert everyone.json["version"] == "canary"


def test_switch_is_atomic_all_methods_together(app):
    # 基线与新版本都在 /multi 上同时提供 GET 和 POST；切换不会让某个
    # 租户在中途看到 "GET 已新版、POST 仍旧版" 或缺方法的状态。
    @app.get("/multi", name="multi")
    def bg(request):
        return text("base-get")

    @app.post("/multi")
    def bp_(request):
        return text("base-post")

    canary = Blueprint("canary")

    @canary.get("/multi", name="multi")
    def cg(request):
        return text("canary-get")

    @canary.post("/multi")
    def cp(request):
        return text("canary-post")

    RouteRollout(app, "r1", canary).check().publish(tenants=["acme"])

    _, get_resp = app.test_client.get("/multi", headers=HEADERS)
    _, post_resp = app.test_client.post("/multi", headers=HEADERS, data="")
    assert get_resp.text == "canary-get"
    assert post_resp.text == "canary-post"

    _, base_get = app.test_client.get("/multi")
    _, base_post = app.test_client.post("/multi", data="")
    assert base_get.text == "base-get"
    assert base_post.text == "base-post"


def test_method_set_follows_bound_version(app):
    # 显式允许方法集合变化：灰度租户在该路径上只能用 GET（POST 405），
    # 其它租户仍可用 POST。
    @app.get("/multi", name="multi")
    def g(request):
        return text("g")

    @app.post("/multi")
    def p(request):
        return text("p")

    canary = Blueprint("canary")

    @canary.get("/multi", name="multi")
    def cg(request):
        return text("cg")

    RouteRollout(app, "r1", canary, strict_methods=False).check().publish(
        tenants=["acme"]
    )

    _, denied = app.test_client.post("/multi", headers=HEADERS, data="")
    assert denied.status == 405
    _, allowed = app.test_client.post("/multi", data="")
    assert allowed.status == 200


# --------------------------------------------------------------------- #
# 撤销与版本回收
# --------------------------------------------------------------------- #
def test_rollback_restores_baseline(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    bp = _canary_bp()
    unit = RouteRollout(app, "r1", bp).check().publish(all_tenants=True)

    _, during = app.test_client.get("/items")
    assert during.json["version"] == "canary"

    unit.rollback()
    assert unit.status is RolloutStatus.ROLLED_BACK
    assert "r1" not in app.rollouts

    _, after = app.test_client.get("/items")
    assert after.json["version"] == "base"


def test_rollback_is_idempotent_and_concurrency_safe(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    unit = RouteRollout(app, "r1", _canary_bp()).check().publish(
        all_tenants=True
    )

    # 并发 / 重复撤销：只有一个执行者真正摘除，其余看到稳定结果，
    # 不会重复释放名称或版本。
    from threading import Thread

    threads = [Thread(target=unit.rollback) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    unit.rollback()

    assert unit.status is RolloutStatus.ROLLED_BACK
    assert "r1" not in app.rollouts
    _, response = app.test_client.get("/items")
    assert response.json["version"] == "base"


def test_rolled_back_unit_cannot_republish(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    unit = RouteRollout(app, "r1", _canary_bp()).check().publish(
        tenants=["acme"]
    )
    unit.rollback()
    with pytest.raises(RolloutError):
        unit.publish(tenants=["acme"])


def test_rollback_releases_name_for_reuse(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    u1 = RouteRollout(app, "r1", _canary_bp("canary", "v2")).check().publish(
        all_tenants=True
    )
    gen1 = u1.generation
    u1.rollback()

    # 同名路由（canary.list_items）现在可以被新单元重新占用。
    u2 = RouteRollout(
        app, "r2", _canary_bp("canary", "v3")
    ).check().publish(all_tenants=True)
    assert u2.generation > gen1

    _, response = app.test_client.get("/items")
    assert response.json["version"] == "v3"


def test_repeat_publish_is_idempotent(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"version": "base"})

    unit = RouteRollout(app, "r1", _canary_bp()).check()
    unit.publish(tenants=["acme"])
    unit.publish(tenants=["acme"])  # 重复发布不得产生重复注册

    assert len(app.rollouts) == 1
    # 重复 publish 可以更新范围，但仍是同一个版本。
    unit.publish(tenants=["acme", "globex"])
    _, g = app.test_client.get("/items", headers={"x-tenant-id": "globex"})
    assert g.json["version"] == "canary"


# --------------------------------------------------------------------- #
# URL 反向生成与异常处理跟随版本
# --------------------------------------------------------------------- #
def test_url_for_follows_bound_version(app):
    app_name = app.name

    @app.get("/items", name="list_items")
    def base(request):
        # 基线请求反向生成基线命名路由。
        return json(
            {
                "version": "base",
                "url": request.app.url_for(f"{app_name}.list_items"),
            }
        )

    bp = Blueprint("canary")

    @bp.get("/items", name="list_items")
    def new(request):
        # 该名称只存在于覆盖表；若 url_for 不跟随绑定版本会抛
        # URLBuildError。这直接证明反向解析走的是绑定版本。
        return json(
            {
                "version": "canary",
                "url": request.app.url_for("canary.list_items"),
                "name": request.name,
            }
        )

    RouteRollout(app, "r1", bp).check().publish(tenants=["acme"])

    _, internal = app.test_client.get("/items", headers=HEADERS)
    assert internal.status_code == 200
    assert internal.json["version"] == "canary"
    assert internal.json["url"] == "/items"
    assert internal.json["name"] == f"{app_name}.canary.list_items"

    _, external = app.test_client.get("/items")
    assert external.status_code == 200
    assert external.json["version"] == "base"
    assert external.json["url"] == "/items"


def test_url_for_resolves_to_version_specific_path(app):
    @app.get("/v1/items", name="list_items")
    def base(request):
        return json(
            {
                "version": "base",
                "url": request.app.url_for(f"{app.name}.list_items"),
            }
        )

    bp = Blueprint("canary")

    @bp.get("/v2/items", name="list_items")
    def new(request):
        return json(
            {
                "version": "canary",
                "url": request.app.url_for("canary.list_items"),
            }
        )

    RouteRollout(app, "r1", bp).check().publish(tenants=["acme"])

    _, internal = app.test_client.get("/v2/items", headers=HEADERS)
    assert internal.json == {"version": "canary", "url": "/v2/items"}

    _, external = app.test_client.get("/v1/items")
    assert external.json == {"version": "base", "url": "/v1/items"}

    # 非灰度租户访问新版路径不应命中覆盖表 → 404。
    _, denied = app.test_client.get("/v2/items")
    assert denied.status_code == 404


def test_exception_handler_follows_bound_version(app):
    class TeapotError(Forbidden):
        pass

    @app.get("/items", name="list_items")
    def base(request):
        raise TeapotError("base")

    @app.exception(TeapotError)
    def global_handler(request, exception):
        return text("global-error", 418)

    bp = Blueprint("canary")

    @bp.get("/items", name="list_items")
    def new(request):
        raise TeapotError("canary")

    @bp.exception(TeapotError)
    def canary_handler(request, exception):
        return text("canary-error", 418)

    RouteRollout(app, "r1", bp).check().publish(tenants=["acme"])

    _, internal = app.test_client.get("/items", headers=HEADERS)
    assert internal.status_code == 418
    assert internal.text == "canary-error"

    _, external = app.test_client.get("/items")
    assert external.status_code == 418
    assert external.text == "global-error"


def test_blueprint_middleware_follows_bound_version(app):
    @app.get("/items", name="list_items")
    def base(request):
        return json({"via": getattr(request.ctx, "via", None)})

    bp = Blueprint("canary")

    @bp.on_request
    def tag(request):
        request.ctx.via = "canary-middleware"

    @bp.get("/items", name="list_items")
    def new(request):
        return json({"via": request.ctx.via})

    RouteRollout(app, "r1", bp).check().publish(tenants=["acme"])

    _, internal = app.test_client.get("/items", headers=HEADERS)
    assert internal.json["via"] == "canary-middleware"

    _, external = app.test_client.get("/items")
    assert external.json["via"] is None


# --------------------------------------------------------------------- #
# 旧版本排空（基于 ASGI，进程内可控并发）
# --------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_in_flight_tracking_and_drain(app):
    gate = asyncio.Event()

    @app.get("/fast", name="fast")
    def fast(request):
        return text("fast")

    bp = Blueprint("canary")

    @bp.get("/slow", name="slow")
    async def slow(request):
        await gate.wait()
        return text("canary-slow")

    unit = RouteRollout(app, "r1", bp).check().publish(all_tenants=True)

    task = asyncio.create_task(app.asgi_client.get("/slow"))
    # 等待请求进入处理并计入在途集合。
    while unit.in_flight == 0:
        await asyncio.sleep(0)

    assert unit.in_flight == 1
    # 排空在请求未完成时应超时，剩余 1 个在途。
    remaining = await unit.drain(timeout=0.05)
    assert remaining == 1

    gate.set()
    _, response = await task
    assert response.text == "canary-slow"

    # 请求结束后在途清零，排空立即返回 0。
    assert unit.in_flight == 0
    assert await unit.drain(timeout=1) == 0


@pytest.mark.asyncio
async def test_in_flight_request_keeps_old_version_after_rollback(app):
    gate = asyncio.Event()

    @app.get("/slow", name="slow")
    async def base(request):
        return text("base-slow")

    bp = Blueprint("canary")

    @bp.get("/slow", name="slow")
    async def slow(request):
        await gate.wait()
        return text("canary-slow")

    unit = RouteRollout(app, "r1", bp).check().publish(all_tenants=True)

    in_flight_request = asyncio.create_task(app.asgi_client.get("/slow"))
    while unit.in_flight == 0:
        await asyncio.sleep(0)

    # 在请求处理途中撤销：新请求立即回落基线，在途请求继续用旧版本。
    unit.rollback()
    _, switched = await app.asgi_client.get("/slow")
    assert switched.text == "base-slow"
    assert unit.in_flight == 1

    gate.set()
    _, response = await in_flight_request
    assert response.text == "canary-slow"
    assert await unit.drain(timeout=1) == 0
