import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.keitaro import KeitaroClient, KeitaroError, get_keitaro
from app.models import Campaign
from app.schemas import CampaignCreate, CampaignCreated, CampaignUpdate

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def make_alias(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "campaign"
    return f"{slug}-{secrets.token_hex(3)}"


def _pick(value: int | None, default: int | None, field: str) -> int:
    v = value if value is not None else default
    if v is None:
        raise HTTPException(422, f"{field} is required (no DEFAULT_{field.upper()} configured)")
    return v


@router.get("")
async def list_campaigns(
    mine: bool = False,
    group_id: int | None = None,
    k: KeitaroClient = Depends(get_keitaro),
    db: Session = Depends(get_db),
):
    """List campaigns from Keitaro. `mine=true` → only those created by this app."""
    items = await k.campaigns()
    if group_id is not None:
        items = [c for c in items if c.get("group_id") == group_id]
    if mine:
        ids = set(db.scalars(select(Campaign.keitaro_id)))
        items = [c for c in items if c["id"] in ids]
    keys = ("id", "name", "alias", "state", "group", "group_id", "domain", "traffic_source_id", "created_at")
    return [{key: c.get(key) for key in keys} for c in items]


@router.post("", response_model=CampaignCreated, status_code=201)
async def create_campaign(
    body: CampaignCreate,
    k: KeitaroClient = Depends(get_keitaro),
    db: Session = Depends(get_db),
):
    domain_id = _pick(body.domain_id, settings.default_domain_id, "domain_id")
    group_id = _pick(body.group_id, settings.default_group_id, "group_id")
    source_id = _pick(body.traffic_source_id, settings.default_traffic_source_id, "traffic_source_id")
    alias = make_alias(body.name)

    campaign = await k.create_campaign(
        {
            "name": body.name,
            "alias": alias,
            "type": "position",
            "state": "active",
            "domain_id": domain_id,
            "group_id": group_id,
            "traffic_source_id": source_id,
        }
    )
    cid = campaign["id"]
    try:
        geo = await k.create_stream(
            {
                "campaign_id": cid,
                "name": f"{body.country} → Google",
                "type": "regular",
                "position": 1,
                "schema": "redirect",
                "action_type": "http",
                "action_payload": settings.geo_redirect_url,
                "filters": [{"name": "country", "mode": "accept", "payload": [body.country]}],
                "collect_clicks": True,
                "state": "active",
            }
        )
        offer = await k.create_stream(
            {
                "campaign_id": cid,
                "name": "Offers",
                "type": "default",
                "position": 2,
                "schema": "landings",
                "action_type": "http",
                "offers": [{"offer_id": body.offer_id, "share": 100, "state": "active"}],
                "collect_clicks": True,
                "state": "active",
            }
        )
    except KeitaroError:
        # don't leave a half-built campaign behind
        await k.delete_campaign(cid)
        raise

    db.add(
        Campaign(
            keitaro_id=cid,
            name=body.name,
            alias=campaign.get("alias", alias),
            country=body.country,
            offer_id=body.offer_id,
            domain_id=domain_id,
            group_id=group_id,
            traffic_source_id=source_id,
            geo_stream_id=geo["id"],
            offer_stream_id=offer["id"],
        )
    )
    db.commit()

    domain = campaign.get("domain")
    return CampaignCreated(
        id=cid,
        alias=campaign.get("alias", alias),
        name=campaign["name"],
        url=f"{domain.rstrip('/')}/{campaign.get('alias', alias)}" if domain else None,
        geo_stream_id=geo["id"],
        offer_stream_id=offer["id"],
        campaign=campaign,
        streams=[geo, offer],
    )


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: int, k: KeitaroClient = Depends(get_keitaro)):
    campaign = await k.campaign(campaign_id)
    campaign["streams"] = await k.campaign_streams(campaign_id)
    return campaign


@router.put("/{campaign_id}")
async def update_campaign(
    campaign_id: int,
    body: CampaignUpdate,
    k: KeitaroClient = Depends(get_keitaro),
    db: Session = Depends(get_db),
):
    payload = body.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(422, "Nothing to update")
    updated = await k.update_campaign(campaign_id, payload)

    local = db.scalar(select(Campaign).where(Campaign.keitaro_id == campaign_id))
    if local:
        for f in ("name", "domain_id", "group_id", "traffic_source_id"):
            if f in payload:
                setattr(local, f, payload[f])
        db.commit()
    return updated


@router.get("/{campaign_id}/streams")
async def campaign_streams(campaign_id: int, k: KeitaroClient = Depends(get_keitaro)):
    return await k.campaign_streams(campaign_id)
