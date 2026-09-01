from app.main import app


def test_internal_runner_route_is_mounted_but_hidden_from_openapi():
    snapshot_path = str(app.url_path_for("get_snapshot", run_id="run-1"))
    openapi_paths = set(app.openapi()["paths"])

    assert snapshot_path == "/internal/runner/runs/run-1/snapshot"
    assert "/internal/runner/runs/{run_id}/snapshot" not in openapi_paths
