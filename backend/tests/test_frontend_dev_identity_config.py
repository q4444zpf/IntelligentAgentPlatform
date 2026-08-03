from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
IDENTITY_VARIABLES = (
    "VITE_DEV_UNIT_ID",
    "VITE_DEV_USER_ROLES",
)


def test_web_image_receives_unit_and_roles_development_identity():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    build_args = compose["services"]["web"]["build"]["args"]
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")

    for variable in IDENTITY_VARIABLES:
        assert variable in build_args
        assert f'ARG {variable}=""' in dockerfile
        assert f"ENV {variable}=${variable}" in dockerfile
