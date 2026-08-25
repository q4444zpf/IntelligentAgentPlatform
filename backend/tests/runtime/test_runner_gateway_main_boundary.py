from app.main import app


def test_internal_runner_route_is_mounted_but_hidden_from_openapi():
    route_path = app.url_path_for("get_snapshot", run_id="run-id")
    openapi_paths = set(app.openapi()["paths"])

    assert str(route_path) == "/internal/runner/runs/run-id/snapshot"
    assert "/internal/runner/runs/{run_id}/snapshot" not in openapi_paths
