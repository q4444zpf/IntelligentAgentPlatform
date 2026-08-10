from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .audit.router import router as audit_router
from .approvals.router import router as approvals_router
from .agents.router import router as agents_router
from .conversations.router import (
    default_run_dispatcher,
    router as conversations_router,
)
from .core.settings import settings
from .model_providers.router import router as model_router
from .mcp.router import router as mcp_router
from .mcp.scheduler import default_mcp_health_scheduler
from .platform.router import router as platform_router
from .skills.router import router as skills_router
from .tools.router import router as tools_router
from .identity.admin_router import router as identity_admin_router
from .identity.auth_router import router as identity_auth_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    default_mcp_health_scheduler.start()
    try:
        yield
    finally:
        default_mcp_health_scheduler.cancel()
        await default_mcp_health_scheduler.wait_closed()
        default_run_dispatcher.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="Intelligent Agent Platform API",
    version="0.2.0",
    lifespan=lifespan,
)
app.state.allow_dev_identity = settings.allow_dev_identity
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def disable_auth_response_caching(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.cookies.get("iap_session"):
        origin = request.headers.get("origin")
        configured = settings.public_base_url
        if origin and configured:
            actual = urlsplit(origin)
            expected = urlsplit(configured)
            actual_port = actual.port or (443 if actual.scheme.lower() == "https" else 80)
            expected_port = expected.port or (443 if expected.scheme.lower() == "https" else 80)
            if (actual.scheme.lower(), (actual.hostname or "").lower(), actual_port) != (expected.scheme.lower(), (expected.hostname or "").lower(), expected_port):
                from starlette.responses import JSONResponse
                return JSONResponse({"detail": "Origin is not allowed"}, status_code=403)
        if not request.url.path.startswith("/api/auth/") and not request.headers.get("x-csrf-token"):
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "CSRF token is required"}, status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    return response
app.include_router(model_router, prefix="/api/models", tags=["models"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(mcp_router, prefix="/api/mcp", tags=["mcp"])
app.include_router(platform_router, prefix="/api/platform", tags=["platform"])
app.include_router(skills_router, prefix="/api/skills", tags=["skills"])
app.include_router(tools_router, prefix="/api/tools", tags=["tools"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.include_router(audit_router, prefix="/api/audit", tags=["audit"])
app.include_router(approvals_router, prefix="/api/approvals", tags=["approvals"])
app.include_router(identity_admin_router, prefix="/api/identity", tags=["identity"])
app.include_router(identity_auth_router, prefix="/api/auth", tags=["auth"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "intelligent-agent-platform-api", "version": "0.2.0"}
