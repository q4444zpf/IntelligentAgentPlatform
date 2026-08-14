from app.main import app


def test_internal_runner_route_is_mounted_but_hidden_from_openapi():
    route_paths = {route.path for route in app.routes}
    openapi_paths = set(app.openapi()["paths"])

    assert "/internal/runner/runs/{run_id}/snapshot" in route_paths
    assert "/internal/runner/runs/{run_id}/snapshot" not in openapi_paths
