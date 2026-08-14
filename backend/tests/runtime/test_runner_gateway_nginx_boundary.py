from pathlib import Path


def test_nginx_does_not_proxy_internal_runner_routes():
    nginx = Path(__file__).parents[3].joinpath("frontend/nginx.conf").read_text(
        encoding="utf-8"
    )
    internal = nginx.split("location /internal/", 1)[1].split("}", 1)[0]
    assert "return 404" in internal
    assert "proxy_pass" not in internal
