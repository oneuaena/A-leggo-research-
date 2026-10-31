"""Loopback-only research API and static desktop UI."""
from __future__ import annotations
import os
from pathlib import Path
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager

if getattr(sys, "frozen", False):
    install_root = Path(sys.executable).resolve().parent.parent
    assets = Path(sys._MEIPASS) / "web-assets"
else:
    install_root = Path(os.environ.get("ALEGGO_RESEARCH_TEST_ROOT") or Path(__file__).resolve().parent.parent)
    assets = Path(__file__).resolve().parent / "web" / "out"
os.environ["ALEGGO_HOME"] = str(install_root)
os.environ["ALEGGO_RESEARCH_HOME"] = str(install_root / "data")
os.environ["ALEGGO_RESEARCH_STANDALONE"] = "1"
os.environ.setdefault("ALEGGO_TOOLS_HOME", str(install_root / "data" / "tools"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from aleggo.api.routers import research
from aleggo.study.standalone_config import ensure_detected_model, read_provider, save_provider
from aleggo.study.research_store import ResearchStore
from aleggo.study.research_collaborator import resume_pending_discussions

@asynccontextmanager
async def lifespan(app: FastAPI):
    store = ResearchStore()
    for project in store.list_projects():
        resume_pending_discussions(project["id"], store)
    yield

app = FastAPI(title="A-leggo Research", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def local_only(request: Request, call_next):
    port = request.scope["server"][1]
    host = request.headers.get("host", "").lower()
    if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
        return JSONResponse({"detail": "Invalid local host"}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin.lower() not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
        return JSONResponse({"detail": "Invalid origin"}, status_code=403)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not origin:
        return JSONResponse({"detail": "Origin required"}, status_code=403)
    return await call_next(request)

class ProviderSettings(BaseModel):
    baseUrl: str = Field(default="", max_length=2000)
    apiKey: str = Field(default="", max_length=4000)
    model: str = Field(default="", max_length=200)
    clearKey: bool = False

@app.get("/api/local-provider")
async def get_provider():
    provider = read_provider()
    configured = bool(provider["api_key"]) or str(provider["base_url"]).lower().startswith("http://")
    effective = str(provider.get("model") or provider.get("detected_model") or "")
    if configured and not effective:
        effective = ensure_detected_model()
        provider = read_provider()
    return {
        "baseUrl": provider["base_url"],
        "configured": configured,
        "model": provider.get("model", ""),
        "effectiveModel": effective,
        "detectedModel": provider.get("detected_model", ""),
        "availableModels": provider.get("available_models", []),
    }

@app.put("/api/local-provider")
async def put_provider(payload: ProviderSettings):
    try:
        return save_provider(payload.baseUrl, payload.apiKey, payload.clearKey, payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@app.post("/api/local-provider/test")
async def test_provider():
    from aleggo.study.model_registry import test_model_access
    return await test_model_access("research.meridian")

@app.get("/api/health")
async def health():
    return {"ok": True, "product": "A-leggo Research"}

app.include_router(research.router, prefix="/api/research")
if assets.is_dir():
    app.mount("/", StaticFiles(directory=assets, html=True), name="ui")

def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--aleggo-math-worker":
        from aleggo.study.math_worker import entrypoint
        entrypoint()
        return
    import uvicorn
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Research service failed to start")
        time.sleep(.1)
    print(f"ALEGGO_FRONTEND_READY_PORT={port}", flush=True)
    thread.join()

if __name__ == "__main__":
    main()
