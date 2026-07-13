from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .model_providers.router import router as model_router

app = FastAPI(title="Intelligent Agent Platform API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(model_router, prefix="/api/models", tags=["models"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

