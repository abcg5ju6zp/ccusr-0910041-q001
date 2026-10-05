import pytest

from sanic import Blueprint, Sanic, text


pytestmark = pytest.mark.xdist_group(name="process_spawning")


@pytest.fixture
def late_app(app: Sanic):
    app.config.TOUCHUP = False
    app.get("/")(lambda _: text(""))
    return app


def test_late_route(late_app: Sanic):
    @late_app.before_server_start
    async def late(app: Sanic):
        @app.get("/late")
        def handler(_):
            return text("late")

    _, response = late_app.test_client.get("/late")
    assert response.status_code == 200
    assert response.text == "late"


def test_late_middleware(late_app: Sanic):
    @late_app.get("/late")
    def handler(request):
        return text(request.ctx.late)

    @late_app.before_server_start
    async def late(app: Sanic):
        @app.on_request
        def handler(request):
            request.ctx.late = "late"

    _, response = late_app.test_client.get("/late")
    assert response.status_code == 200
    assert response.text == "late"


def test_late_signal(late_app: Sanic):
    @late_app.get("/late")
    def handler(request):
        return text(request.ctx.late)

    @late_app.before_server_start
    async def late(app: Sanic):
        @app.signal("http.lifecycle.request")
        def handler(request):
            request.ctx.late = "late"

    _, response = late_app.test_client.get("/late")
    assert response.status_code == 200
    assert response.text == "late"


def test_late_blueprint_group_failure_leaves_no_members(late_app: Sanic):
    bp1 = Blueprint("bp1", url_prefix="/bp1")
    bp2 = Blueprint("bp2", url_prefix="/bp2")

    @bp1.get("/one")
    def one(_):
        return text("one")

    @bp2.get("/two")
    def two(_):
        return text("two")

    @bp1.on_request
    def mark(request):
        request.ctx.marked = True

    late_app.blueprint(Blueprint("shared"))
    state = {}

    @late_app.before_server_start
    async def late(app: Sanic):
        state["named_before"] = dict(app.named_request_middleware)
        state["mw_before"] = list(app.request_middleware)
        group = Blueprint.group(bp1, bp2, Blueprint("shared"))
        with pytest.raises(AssertionError):
            app.blueprint(group)
        state["named_after"] = dict(app.named_request_middleware)
        state["mw_after"] = list(app.request_middleware)
        state["blueprints"] = list(app.blueprints)

    _, response = late_app.test_client.get("/")
    assert response.status_code == 200

    _, response = late_app.test_client.get("/bp1/one")
    assert response.status_code == 404

    _, response = late_app.test_client.get("/bp2/two")
    assert response.status_code == 404

    assert state["named_after"] == state["named_before"]
    assert state["mw_after"] == state["mw_before"]
    assert state["blueprints"] == ["shared"]
