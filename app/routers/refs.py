from fastapi import APIRouter, Depends

from app.keitaro import KeitaroClient, get_keitaro

router = APIRouter(prefix="/api/refs", tags=["reference data"])


def _brief(items: list[dict], *extra: str) -> list[dict]:
    keys = ("id", "name", "state", *extra)
    return [{k: i.get(k) for k in keys} for i in items]


@router.get("/domains")
async def domains(k: KeitaroClient = Depends(get_keitaro)):
    return _brief(await k.domains())


@router.get("/groups")
async def groups(k: KeitaroClient = Depends(get_keitaro)):
    return _brief(await k.groups())


@router.get("/traffic_sources")
async def traffic_sources(k: KeitaroClient = Depends(get_keitaro)):
    return _brief(await k.traffic_sources())


@router.get("/offers")
async def offers(active_only: bool = True, k: KeitaroClient = Depends(get_keitaro)):
    items = await k.offers()
    if active_only:
        items = [o for o in items if o.get("state") == "active"]
    return _brief(items)
