import pytest

from pytest import raises
from sanic_routing.exceptions import RouteExists

from sanic.app import Sanic
from sanic.blueprint_group import BlueprintGroup
from sanic.blueprints import Blueprint
from sanic.exceptions import (
    BadRequest,
    Forbidden,
    InvalidSignal,
    SanicException,
    ServerError,
)
from sanic.request import Request
from sanic.response import HTTPResponse, text


MIDDLEWARE_INVOKE_COUNTER = {"request": 0, "response": 0}

AUTH = "dGVzdDp0ZXN0Cg=="


def _full_bp(name: str, url_prefix: str | None = None) -> Blueprint:
    bp = Blueprint(name, url_prefix=url_prefix or f"/{name}")

    @bp.get("/")
    async def handler(request):
        return text(name)

    @bp.listener("before_server_start")
    async def listener(app, loop):
        ...

    @bp.listener("main_process_start")
    async def main_listener(app):
        ...

    @bp.middleware("request")
    async def middleware(request):
        ...

    @bp.exception(NotImplementedError)
    async def exception_handler(request, exception):
        return text("error")

    @bp.signal("my.custom.signal")
    async def signal_handler():
        ...

    return bp


def _registration_state(app: Sanic):
    return {
        "blueprints": sorted(app.blueprints),
        "order": [bp.name for bp in app._blueprint_order],
        "routes": sorted(route.path for route in app.router.routes),
        "route_names": sorted(
            route.name or "" for route in app.router.routes
        ),
        "request_middleware": list(app.request_middleware),
        "response_middleware": list(app.response_middleware),
        "named_request_middleware": {
            name: list(middleware)
            for name, middleware in app.named_request_middleware.items()
        },
        "named_response_middleware": {
            name: list(middleware)
            for name, middleware in app.named_response_middleware.items()
        },
        "error_handlers": dict(app.error_handler.cached_handlers),
        "listeners": {
            event: list(listeners)
            for event, listeners in app.listeners.items()
        },
        "signals": {
            segments: tuple(route.handler for route in group.routes)
            for segments, group in app.signal_router.groups.items()
        },
        "signal_names": sorted(app.signal_router.name_index),
        "future_registry": set(app._future_registry),
        "websocket_enabled": app.websocket_enabled,
    }


def test_bp_group_indexing(app: Sanic):
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")

    group = Blueprint.group(blueprint_1, blueprint_2)
    assert group[0] == blueprint_1

    with raises(expected_exception=IndexError):
        _ = group[3]


def test_bp_group_set_item_by_index(app: Sanic):
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")

    group = Blueprint.group(blueprint_1, blueprint_2)
    group[0] = blueprint_2

    assert group[0] == blueprint_2


def test_bp_group_with_additional_route_params(app: Sanic):
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")

    @blueprint_1.route(
        "/request_path", methods=frozenset({"PUT", "POST"}), version=2
    )
    def blueprint_1_v2_method_with_put_and_post(request: Request):
        if request.method == "PUT":
            return text("PUT_OK")
        elif request.method == "POST":
            return text("POST_OK")

    @blueprint_2.route(
        "/route/<param>", methods=frozenset({"DELETE", "PATCH"}), name="test"
    )
    def blueprint_2_named_method(request: Request, param):
        if request.method == "DELETE":
            return text(f"DELETE_{param}")
        elif request.method == "PATCH":
            return text(f"PATCH_{param}")

    blueprint_group = Blueprint.group(
        blueprint_1, blueprint_2, url_prefix="/api"
    )

    @blueprint_group.middleware("request")
    def authenticate_request(request: Request):
        global AUTH
        auth = request.headers.get("authorization")
        if auth:
            # Dummy auth check. We can have anything here and it's fine.
            if AUTH not in auth:
                return text("Unauthorized", status=401)
        else:
            return text("Unauthorized", status=401)

    @blueprint_group.middleware("response")
    def enhance_response_middleware(request: Request, response: HTTPResponse):
        response.headers.add("x-test-middleware", "value")

    app.blueprint(blueprint_group)

    header = {"authorization": " ".join(["Basic", AUTH])}
    _, response = app.test_client.put(
        "/v2/api/bp1/request_path", headers=header
    )
    assert response.text == "PUT_OK"
    assert response.headers.get("x-test-middleware") == "value"

    _, response = app.test_client.post(
        "/v2/api/bp1/request_path", headers=header
    )
    assert response.text == "POST_OK"

    _, response = app.test_client.delete("/api/bp2/route/bp2", headers=header)
    assert response.text == "DELETE_bp2"

    _, response = app.test_client.patch("/api/bp2/route/bp2", headers=header)
    assert response.text == "PATCH_bp2"

    _, response = app.test_client.put("/v2/api/bp1/request_path")
    assert response.status == 401


def test_bp_group(app: Sanic):
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")

    @blueprint_1.route("/")
    def blueprint_1_default_route(request):
        return text("BP1_OK")

    @blueprint_1.route("/invalid")
    def blueprint_1_error(request: Request):
        raise BadRequest("Invalid")

    @blueprint_2.route("/")
    def blueprint_2_default_route(request):
        return text("BP2_OK")

    @blueprint_2.route("/error")
    def blueprint_2_error(request: Request):
        raise ServerError("Error")

    blueprint_group_1 = Blueprint.group(
        blueprint_1, blueprint_2, url_prefix="/bp"
    )

    blueprint_3 = Blueprint("blueprint_3", url_prefix="/bp3")

    @blueprint_group_1.exception(BadRequest)
    def handle_group_exception(request, exception):
        return text("BP1_ERR_OK")

    @blueprint_group_1.middleware("request")
    def blueprint_group_1_middleware(request):
        global MIDDLEWARE_INVOKE_COUNTER
        MIDDLEWARE_INVOKE_COUNTER["request"] += 1

    @blueprint_group_1.middleware
    def blueprint_group_1_middleware_not_called(request):
        global MIDDLEWARE_INVOKE_COUNTER
        MIDDLEWARE_INVOKE_COUNTER["request"] += 1

    @blueprint_group_1.on_request
    def blueprint_group_1_convenience_1(request):
        global MIDDLEWARE_INVOKE_COUNTER
        MIDDLEWARE_INVOKE_COUNTER["request"] += 1

    @blueprint_group_1.on_request()
    def blueprint_group_1_convenience_2(request):
        global MIDDLEWARE_INVOKE_COUNTER
        MIDDLEWARE_INVOKE_COUNTER["request"] += 1

    @blueprint_3.route("/")
    def blueprint_3_default_route(request):
        return text("BP3_OK")

    @blueprint_3.route("/forbidden")
    def blueprint_3_forbidden(request: Request):
        raise Forbidden("Forbidden")

    blueprint_group_2 = Blueprint.group(
        blueprint_group_1, blueprint_3, url_prefix="/api"
    )

    @blueprint_group_2.exception(SanicException)
    def handle_non_handled_exception(request, exception):
        return text("BP2_ERR_OK")

    @blueprint_group_2.middleware("response")
    def blueprint_group_2_middleware(request, response):
        global MIDDLEWARE_INVOKE_COUNTER
        MIDDLEWARE_INVOKE_COUNTER["response"] += 1

    @blueprint_group_2.on_response
    def blueprint_group_2_middleware_convenience_1(request, response):
        global MIDDLEWARE_INVOKE_COUNTER
        MIDDLEWARE_INVOKE_COUNTER["response"] += 1

    @blueprint_group_2.on_response()
    def blueprint_group_2_middleware_convenience_2(request, response):
        global MIDDLEWARE_INVOKE_COUNTER
        MIDDLEWARE_INVOKE_COUNTER["response"] += 1

    app.blueprint(blueprint_group_2)

    @app.route("/")
    def app_default_route(request):
        return text("APP_OK")

    _, response = app.test_client.get("/")
    assert response.text == "APP_OK"

    _, response = app.test_client.get("/api/bp/bp1")
    assert response.text == "BP1_OK"

    _, response = app.test_client.get("/api/bp/bp1/invalid")
    assert response.text == "BP1_ERR_OK"

    _, response = app.test_client.get("/api/bp/bp2")
    assert response.text == "BP2_OK"

    _, response = app.test_client.get("/api/bp/bp2/error")
    assert response.text == "BP2_ERR_OK"

    _, response = app.test_client.get("/api/bp3")
    assert response.text == "BP3_OK"

    _, response = app.test_client.get("/api/bp3/forbidden")
    assert response.text == "BP2_ERR_OK"

    assert MIDDLEWARE_INVOKE_COUNTER["response"] == 18
    assert MIDDLEWARE_INVOKE_COUNTER["request"] == 16


def test_bp_group_list_operations(app: Sanic):
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")

    @blueprint_1.route("/")
    def blueprint_1_default_route(request):
        return text("BP1_OK")

    @blueprint_2.route("/")
    def blueprint_2_default_route(request):
        return text("BP2_OK")

    blueprint_group_1 = Blueprint.group(
        blueprint_1, blueprint_2, url_prefix="/bp"
    )

    blueprint_3 = Blueprint("blueprint_2", url_prefix="/bp3")

    @blueprint_3.route("/second")
    def blueprint_3_second_route(request):
        return text("BP3_OK")

    assert len(blueprint_group_1) == 2

    blueprint_group_1.append(blueprint_3)
    assert len(blueprint_group_1) == 3

    del blueprint_group_1[2]
    assert len(blueprint_group_1) == 2

    blueprint_group_1[1] = blueprint_3
    assert len(blueprint_group_1) == 2

    assert blueprint_group_1.url_prefix == "/bp"


def test_bp_group_as_list():
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")
    blueprint_group_1 = Blueprint.group([blueprint_1, blueprint_2])
    assert len(blueprint_group_1) == 2


def test_bp_group_as_nested_group():
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")
    blueprint_group_1 = Blueprint.group(
        Blueprint.group(blueprint_1, blueprint_2)
    )
    assert len(blueprint_group_1) == 1


def test_blueprint_group_insert():
    blueprint_1 = Blueprint(
        "blueprint_1", url_prefix="/bp1", strict_slashes=True, version=1
    )
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")
    blueprint_3 = Blueprint("blueprint_3", url_prefix=None)
    group = BlueprintGroup(
        url_prefix="/test", version=1.3, strict_slashes=False
    )
    group.insert(0, blueprint_1)
    group.insert(0, blueprint_2)
    group.insert(0, blueprint_3)

    @blueprint_1.route("/")
    def blueprint_1_default_route(request):
        return text("BP1_OK")

    @blueprint_2.route("/")
    def blueprint_2_default_route(request):
        return text("BP2_OK")

    @blueprint_3.route("/")
    def blueprint_3_default_route(request):
        return text("BP3_OK")

    app = Sanic("PropTest")
    app.blueprint(group)
    app.router.finalize()

    routes = [(route.path, route.strict) for route in app.router.routes]

    assert len(routes) == 3
    assert ("v1/test/bp1/", True) in routes
    assert ("v1.3/test/bp2", False) in routes
    assert ("v1.3/test", False) in routes


def test_bp_group_properties():
    blueprint_1 = Blueprint("blueprint_1", url_prefix="/bp1")
    blueprint_2 = Blueprint("blueprint_2", url_prefix="/bp2")
    group = Blueprint.group(
        blueprint_1,
        blueprint_2,
        version=1,
        version_prefix="/api/v",
        url_prefix="/grouped",
        strict_slashes=True,
    )
    primary = Blueprint.group(group, url_prefix="/primary")

    @blueprint_1.route("/")
    def blueprint_1_default_route(request):
        return text("BP1_OK")

    @blueprint_2.route("/")
    def blueprint_2_default_route(request):
        return text("BP2_OK")

    app = Sanic("PropTest")
    app.blueprint(group)
    app.blueprint(primary)
    app.router.finalize()

    routes = [route.path for route in app.router.routes]

    assert len(routes) == 4
    assert "api/v1/grouped/bp1/" in routes
    assert "api/v1/grouped/bp2/" in routes
    assert "api/v1/primary/grouped/bp1" in routes
    assert "api/v1/primary/grouped/bp2" in routes


def test_nested_bp_group_properties():
    one = Blueprint("one", url_prefix="/one")
    two = Blueprint.group(one)
    three = Blueprint.group(two, url_prefix="/three")

    @one.route("/four")
    def handler(request):
        return text("pi")

    app = Sanic("PropTest")
    app.blueprint(three)
    app.router.finalize()

    routes = [route.path for route in app.router.routes]
    assert routes == ["three/one/four"]


@pytest.mark.asyncio
async def test_multiple_nested_bp_group():
    bp1 = Blueprint("bp1", url_prefix="/bp1")
    bp2 = Blueprint("bp2", url_prefix="/bp2")

    bp1.add_route(lambda _: ..., "/", name="route1")
    bp2.add_route(lambda _: ..., "/", name="route2")

    group_a = Blueprint.group(
        bp1, bp2, url_prefix="/group-a", name_prefix="group-a"
    )
    group_b = Blueprint.group(
        bp1, bp2, url_prefix="/group-b", name_prefix="group-b"
    )

    app = Sanic("PropTest")
    app.blueprint(group_a)
    app.blueprint(group_b)

    await app._startup()

    routes = [route.path for route in app.router.routes]
    assert routes == [
        "group-a/bp1",
        "group-a/bp2",
        "group-b/bp1",
        "group-b/bp2",
    ]
    names = [route.name for route in app.router.routes]
    assert names == [
        "PropTest.group-a_bp1.route1",
        "PropTest.group-a_bp2.route2",
        "PropTest.group-b_bp1.route1",
        "PropTest.group-b_bp2.route2",
    ]


def test_bp_group_registration_atomic_on_name_conflict(app: Sanic):
    existing = _full_bp("existing")
    app.blueprint(existing)
    state_before = _registration_state(app)

    bp1 = _full_bp("bp1")
    bp2 = _full_bp("existing")

    with raises(AssertionError, match="already registered"):
        app.blueprint(Blueprint.group(bp1, bp2, url_prefix="/api"))

    assert _registration_state(app) == state_before
    assert not bp1.registered
    assert bp1.routes == []
    assert bp1.middlewares == []
    assert bp1.exceptions == []
    assert bp1.listeners == {}

    _, response = app.test_client.get("/api/bp1")
    assert response.status == 404
    _, response = app.test_client.get("/existing")
    assert response.status == 200


def test_bp_group_registration_atomic_on_route_conflict(app: Sanic):
    existing = _full_bp("existing")
    app.blueprint(existing)
    state_before = _registration_state(app)

    bp1 = _full_bp("bp1")
    bp2 = _full_bp("bp2", url_prefix="/bp1")

    with raises(RouteExists):
        app.blueprint(Blueprint.group(bp1, bp2))

    assert _registration_state(app) == state_before
    assert list(app.blueprints) == ["existing"]
    assert [bp.name for bp in app._blueprint_order] == ["existing"]
    assert not bp1.registered
    assert not bp2.registered

    _, response = app.test_client.get("/bp1")
    assert response.status == 404
    _, response = app.test_client.get("/existing")
    assert response.status == 200


def test_bp_group_registration_failure_is_deterministic_on_retry(app: Sanic):
    bp1 = _full_bp("bp1")
    bp2 = _full_bp("bp2", url_prefix="/bp1")
    group = Blueprint.group(bp1, bp2)

    with raises(RouteExists):
        app.blueprint(group)
    state_after_first_failure = _registration_state(app)

    with raises(RouteExists):
        app.blueprint(group)
    assert _registration_state(app) == state_after_first_failure


def test_bp_group_registration_retry_after_fix(app: Sanic):
    existing = _full_bp("existing")
    app.blueprint(existing)

    bp1 = _full_bp("bp1")
    bp2 = _full_bp("existing")

    with raises(AssertionError, match="already registered"):
        app.blueprint(Blueprint.group(bp1, bp2, url_prefix="/api"))

    bp2_fixed = _full_bp("bp2")
    app.blueprint(Blueprint.group(bp1, bp2_fixed, url_prefix="/api"))

    paths = [route.path for route in app.router.routes]
    assert sorted(paths) == ["api/bp1", "api/bp2", "existing"]
    assert list(app.blueprints) == ["existing", "bp1", "bp2"]

    _, response = app.test_client.get("/api/bp1")
    assert response.status == 200
    _, response = app.test_client.get("/api/bp2")
    assert response.status == 200


def test_nested_bp_group_registration_atomic(app: Sanic):
    existing = _full_bp("existing")
    app.blueprint(existing)
    state_before = _registration_state(app)

    outer_good = _full_bp("outer_good")
    inner_good = _full_bp("inner_good")
    inner_bad = _full_bp("existing")
    inner = Blueprint.group(inner_good, inner_bad, url_prefix="/inner")
    outer = Blueprint.group(outer_good, inner, url_prefix="/outer")

    with raises(AssertionError, match="already registered"):
        app.blueprint(outer)

    assert _registration_state(app) == state_before
    assert not outer_good.registered
    assert not inner_good.registered

    _, response = app.test_client.get("/outer/outer_good")
    assert response.status == 404
    _, response = app.test_client.get("/outer/inner/inner_good")
    assert response.status == 404


def test_bp_group_registration_atomic_on_listener_error(app: Sanic):
    bp1 = _full_bp("bp1")
    bp2 = _full_bp("bp2")

    @bp2.listener("not_a_valid_event")
    async def bad_listener(app, loop):
        ...

    with raises(KeyError):
        app.blueprint(Blueprint.group(bp1, bp2))

    assert app.blueprints == {}
    assert app.router.routes == ()
    assert not bp1.registered
    assert not bp2.registered


def test_bp_group_registration_atomic_on_signal_error(app: Sanic):
    bp1 = _full_bp("bp1")
    bp2 = _full_bp("bp2")

    @bp2.signal("this.has.too.many.parts")
    async def bad_signal():
        ...

    with raises(InvalidSignal):
        app.blueprint(Blueprint.group(bp1, bp2))

    assert app.blueprints == {}
    assert app.router.routes == ()
    assert not bp1.registered
    assert not bp2.registered


def test_bp_group_registration_atomic_on_duplicate_exception_handler(
    app: Sanic,
):
    bp1 = _full_bp("bp1")
    bp2 = _full_bp("bp2")

    @bp2.exception(NotImplementedError)
    async def duplicate_handler(request, exception):
        return text("duplicate")

    with raises(ServerError, match="Duplicate exception handler"):
        app.blueprint(Blueprint.group(bp1, bp2))

    assert app.blueprints == {}
    assert app.router.routes == ()
    assert not bp1.registered
    assert not bp2.registered


def test_bp_group_registration_atomic_on_command(app: Sanic):
    bp1 = _full_bp("bp1")
    bp2 = _full_bp("bp2")

    @bp2.command(name="cmd")
    async def command():
        ...

    with raises(SanicException, match="Registering commands"):
        app.blueprint(Blueprint.group(bp1, bp2))

    assert app.blueprints == {}
    assert app.router.routes == ()
    assert not bp1.registered
    assert not bp2.registered


def test_bp_group_registration_atomic_on_websocket(app: Sanic):
    existing = _full_bp("existing")
    app.blueprint(existing)
    state_before = _registration_state(app)

    bp1 = Blueprint("bp1", url_prefix="/bp1")

    @bp1.websocket("/ws")
    async def websocket_handler(request, ws):
        ...

    bp2 = _full_bp("existing")

    with raises(AssertionError, match="already registered"):
        app.blueprint(Blueprint.group(bp1, bp2))

    assert _registration_state(app) == state_before
    assert app.websocket_enabled is False
    assert not bp1.registered


def test_bp_group_failed_late_registration_keeps_app_consistent(app: Sanic):
    app.config.TOUCHUP = False

    @app.get("/home")
    async def home(request):
        return text("home")

    bp1 = _full_bp("bp1")
    bp2 = _full_bp("bp2", url_prefix="/bp1")

    @app.before_server_start
    async def register_late(app):
        with raises(RouteExists):
            app.blueprint(Blueprint.group(bp1, bp2))

    _, response = app.test_client.get("/home")
    assert response.status == 200
    assert response.text == "home"

    _, response = app.test_client.get("/bp1")
    assert response.status == 404
    assert app.blueprints == {}
    assert not bp1.registered
    assert not bp2.registered


def test_bp_group_registration_atomic_with_static(
    app: Sanic, static_file_directory
):
    existing = _full_bp("existing")
    app.blueprint(existing)
    state_before = _registration_state(app)

    bp1 = Blueprint("bp1", url_prefix="/bp1")
    bp1.static("/files", static_file_directory, name="files")
    bp2 = _full_bp("existing")

    with raises(AssertionError, match="already registered"):
        app.blueprint(Blueprint.group(bp1, bp2))

    assert _registration_state(app) == state_before
    assert bp1.routes == []

    _, response = app.test_client.get("/bp1/files/test.file")
    assert response.status == 404
