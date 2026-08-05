from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .audit.router import router as audit_router
from .agents.router import router as agents_router
from .conversations.router import (
    default_run_dispatcher,
    router as conversations_router,
)
from .core.settings import settings
from .model_providers.router import router as model_router
from .mcp.router import router as mcp_router
from .platform.router import router as platform_router
from .skills.router import router as skills_router
from .tools.router import router as tools_router
from .identity.admin_router import router as identity_admin_router

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
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
app.include_router(model_router, prefix="/api/models", tags=["models"])
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(mcp_router, prefix="/api/mcp", tags=["mcp"])
app.include_router(platform_router, prefix="/api/platform", tags=["platform"])
app.include_router(skills_router, prefix="/api/skills", tags=["skills"])
app.include_router(tools_router, prefix="/api/tools", tags=["tools"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.include_router(audit_router, prefix="/api/audit", tags=["audit"])
app.include_router(identity_admin_router, prefix="/api/identity", tags=["identity"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "intelligent-agent-platform-api", "version": "0.2.0"}
