from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.keitaro import KeitaroError
from app.routers import campaigns, refs, streams

app = FastAPI(title="Adrobot — Keitaro wrapper", version="0.1.0")


@app.exception_handler(KeitaroError)
async def keitaro_error(_: Request, exc: KeitaroError):
    status = exc.status if exc.status in (400, 404, 409, 422) else 502
    return JSONResponse(status_code=status, content={"detail": exc.detail, "keitaro_status": exc.status})


app.include_router(refs.router)
app.include_router(campaigns.router)
app.include_router(streams.router)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/config")
async def config():
    """Defaults the UI pre-selects in the campaign creator."""
    return {
        "domain_id": settings.default_domain_id,
        "group_id": settings.default_group_id,
        "traffic_source_id": settings.default_traffic_source_id,
        "geo_redirect_url": settings.geo_redirect_url,
    }


# UI (mounted last so /api routes win)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
