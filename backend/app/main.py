from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agents.router import router as agents_router
from .conversations.router import router as conversations_router
from .core.settings import settings
from .model_providers.router import router as model_router
from .mcp.router import router as mcp_router
from .platform.router import router as platform_router
from .skills.router import router as skills_router

app = FastAPI(title="Intelligent Agent Platform API", version="0.2.0")
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
app.include_router(conversations_router, prefix="/api", tags=["conversations"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "intelligent-agent-platform-api", "version": "0.2.0"}
