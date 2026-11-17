"""Offer editor for a stream.

All edits (add / remove / restore / hide / show / pin) change a local draft only.
Committed removals stay in the draft as "deleted" rows: they can be returned to the stream later
or dismissed from the list (and shown again).
`POST .../draft/commit` pushes the draft to Keitaro in one PUT and logs the change.
`POST .../history/{id}/load` loads any past version back into the draft.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.keitaro import KeitaroClient, KeitaroError, get_keitaro
from app.models import Campaign, DraftOffer, OfferChange, StreamDraft
from app.shares import is_visible, pinned_total, recalc, visible_total

router = APIRouter(prefix="/api/streams", tags=["offer editor"])


# ---------- schemas ----------
class OffersAdd(BaseModel):
    offer_ids: list[int] = Field(min_length=1)


class PinBody(BaseModel):
    share: int = Field(ge=0, le=100)


class LoadVersion(BaseModel):
    which: str = Field(default="before", pattern="^(before|after)$")


# ---------- helpers ----------
def normalize(offers: list[dict] | None) -> list[dict]:
    """Keitaro offers -> comparable [{offer_id, share, state}] sorted by offer_id."""
    return sorted(
        ({"offer_id": o["offer_id"], "share": int(o.get("share") or 0), "state": o.get("state") or "active"} for o in offers or []),
        key=lambda o: o["offer_id"],
    )


def base_pins(draft: StreamDraft) -> dict[int, bool]:
    """Pins as of the last commit/sync (stored alongside the base snapshot)."""
    return {o["offer_id"]: bool(o.get("pinned")) for o in draft.base or []}


def with_pins(offers: list[dict], pins: dict[int, bool]) -> list[dict]:
    return [{**o, "pinned": pins.get(o["offer_id"], False)} for o in offers]


def payload(draft: StreamDraft) -> list[dict]:
    """What would be sent to Keitaro for this draft (removed offers are dropped)."""
    return [
        {"offer_id": r.offer_id, "share": r.share, "state": "disabled" if r.hidden else "active"}
        for r in draft.offers
        if not r.removed
    ]


def is_dirty(draft: StreamDraft) -> bool:
    """Draft differs from Keitaro (needs a PUT)."""
    return normalize(payload(draft)) != normalize(draft.base)


def pins_dirty(draft: StreamDraft) -> bool:
    """Pins changed since the last commit (local-only, no Keitaro call needed)."""
    saved = base_pins(draft)
    return any(r.pinned != saved.get(r.offer_id, False) for r in draft.offers if not r.removed)


def rows(draft: StreamDraft) -> list[DraftOffer]:
    return sorted(draft.offers, key=lambda r: r.position)


def fill_rows(draft: StreamDraft, offers: list[dict], keep_pins: dict[int, bool] | None = None) -> None:
    """Make the draft mirror `offers` (Keitaro order). Rows are updated in place; committed deletions are kept."""
    keep_pins = keep_pins or {}
    existing = {r.offer_id: r for r in draft.offers}
    ids = {o["offer_id"] for o in offers}
    for r in list(draft.offers):
        if r.offer_id not in ids and not r.deleted_at:
            draft.offers.remove(r)
    for pos, o in enumerate(offers):
        r = existing.get(o["offer_id"])
        if r is None:
            r = DraftOffer(offer_id=o["offer_id"])
            draft.offers.append(r)
        r.share = int(o.get("share") or 0)
        r.hidden = (o.get("state") or "active") != "active"
        r.pinned = keep_pins.get(o["offer_id"], False)
        r.removed, r.deleted_at, r.dismissed = False, None, False
        r.position = pos
    pos = len(offers)
    for r in rows(draft):
        if r.offer_id not in ids:  # deleted earlier — stays restorable
            r.removed, r.share, r.pinned, r.position = True, 0, False, pos
            pos += 1


def is_deleted(draft: StreamDraft, r: DraftOffer) -> bool:
    """Removal already committed to Keitaro (vs. pending removal)."""
    return r.removed and r.offer_id not in {o["offer_id"] for o in draft.base}


def view(draft: StreamDraft, conflict: bool = False) -> dict:
    base = {o["offer_id"]: o for o in normalize(draft.base)}
    offers = []
    for r in rows(draft):
        b = base.get(r.offer_id)
        if r.removed:
            status = "removed" if b else "deleted"
        elif b is None:
            status = "new"
        elif b["share"] != r.share or (b["state"] != "active") != r.hidden:
            status = "changed"
        else:
            status = "synced"
        offers.append(
            {
                "offer_id": r.offer_id,
                "share": r.share,
                "pinned": r.pinned,
                "hidden": r.hidden,
                "removed": r.removed,
                "dismissed": r.dismissed,
                "deleted_at": r.deleted_at.isoformat(sep=" ", timespec="seconds") if r.deleted_at else None,
                "status": status,
                "base_share": b["share"] if b else None,
                "base_state": b["state"] if b else None,
            }
        )
    total = visible_total(draft.offers)
    keitaro_dirty = is_dirty(draft)
    dirty = keitaro_dirty or pins_dirty(draft)
    visible = [r for r in draft.offers if is_visible(r)]
    problems = []
    if not visible:
        problems.append("В потоке нет ни одного активного оффера")
    if total != 100:
        problems.append(f"Сумма долей {total}%, должна быть 100%")
    return {
        "stream_id": draft.stream_id,
        "campaign_id": draft.keitaro_campaign_id,
        "offers": offers,
        "total": total,
        "dirty": dirty,
        "keitaro_dirty": keitaro_dirty,
        "conflict": conflict,
        "problems": problems,
        "can_commit": dirty and not problems,
    }


async def fetch_offers(k: KeitaroClient, stream_id: int) -> tuple[dict, list[dict], list[dict]]:
    """-> (stream, offers normalized & sorted by id, offers in Keitaro order)."""
    stream = await k.stream(stream_id)
    if stream.get("schema") != "landings":
        raise HTTPException(422, f"Stream {stream_id} has schema '{stream.get('schema')}', offers live in 'landings' streams")
    current = normalize(stream.get("offers"))
    order = [o["offer_id"] for o in stream.get("offers") or []]
    return stream, current, sorted(current, key=lambda o: order.index(o["offer_id"]))


def backfill_deleted(draft: StreamDraft, db: Session) -> None:
    """Offers that history shows were in the stream but are gone now -> deleted rows (for pre-existing data)."""
    have = {r.offer_id for r in draft.offers}
    pos = max((r.position for r in draft.offers), default=-1)
    changes = db.scalars(select(OfferChange).where(OfferChange.stream_id == draft.stream_id).order_by(OfferChange.id))
    for c in changes:
        for o in (c.offers_before or []) + (c.offers_after or []):
            if o["offer_id"] not in have:
                have.add(o["offer_id"])
                pos += 1
                draft.offers.append(
                    DraftOffer(offer_id=o["offer_id"], share=0, removed=True, deleted_at=c.created_at, position=pos)
                )


async def load_draft(stream_id: int, k: KeitaroClient, db: Session) -> tuple[StreamDraft, bool]:
    """Get the draft, creating it from Keitaro or re-syncing a clean draft if Keitaro changed."""
    stream, current, current_ordered = await fetch_offers(k, stream_id)
    draft = db.get(StreamDraft, stream_id)
    conflict = False
    if draft is None:
        draft = StreamDraft(stream_id=stream_id, keitaro_campaign_id=stream.get("campaign_id"), base=current)
        db.add(draft)
        fill_rows(draft, current_ordered)
    elif current != normalize(draft.base):
        if is_dirty(draft):
            conflict = True  # someone changed Keitaro while we have unsaved edits
        else:
            pins = base_pins(draft)
            draft.base = with_pins(current, pins)
            fill_rows(draft, current_ordered, keep_pins=pins)
    backfill_deleted(draft, db)
    db.commit()
    return draft, conflict


async def existing_draft(stream_id: int, k: KeitaroClient, db: Session) -> StreamDraft:
    draft = db.get(StreamDraft, stream_id)
    if draft is None:
        draft, _ = await load_draft(stream_id, k, db)
    return draft


def find_row(draft: StreamDraft, offer_id: int) -> DraftOffer:
    for r in draft.offers:
        if r.offer_id == offer_id:
            return r
    raise HTTPException(404, f"Offer {offer_id} is not in stream {draft.stream_id}")


def save(draft: StreamDraft, db: Session) -> dict:
    recalc(rows(draft))
    db.commit()
    return view(draft)


# ---------- draft ----------
@router.get("/{stream_id}/draft")
async def get_draft(stream_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft, conflict = await load_draft(stream_id, k, db)
    return view(draft, conflict)


@router.post("/{stream_id}/draft/offers")
async def add_offers(stream_id: int, body: OffersAdd, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    have = {r.offer_id: r for r in draft.offers}
    added = 0
    for oid in dict.fromkeys(body.offer_ids):
        if oid in have:
            if have[oid].removed:  # re-adding a removed offer = restore it
                have[oid].removed = have[oid].dismissed = False
                added += 1
            continue
        try:
            await k.offer(oid)
        except KeitaroError as e:
            raise HTTPException(404, f"Offer {oid} not found in Keitaro") from e
        draft.offers.append(DraftOffer(offer_id=oid, share=0, position=max((r.position for r in draft.offers), default=-1) + 1))
        added += 1
    if not added:
        raise HTTPException(409, "All given offers are already in the stream")
    return save(draft, db)


@router.delete("/{stream_id}/draft/offers/{offer_id}")
async def remove_offer(stream_id: int, offer_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    row = find_row(draft, offer_id)
    if offer_id in {o["offer_id"] for o in draft.base} or row.deleted_at:
        row.removed = True  # keep it so it can be restored
    else:
        draft.offers.remove(row)  # added in this draft only — just drop it
    return save(draft, db)


@router.post("/{stream_id}/draft/offers/{offer_id}/restore")
async def restore_offer(stream_id: int, offer_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    row = find_row(draft, offer_id)
    row.removed = row.dismissed = False
    return save(draft, db)


@router.post("/{stream_id}/draft/offers/{offer_id}/dismiss")
async def dismiss_offer(stream_id: int, offer_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    """Hide a deleted offer from the list (it can still be shown and returned)."""
    draft = await existing_draft(stream_id, k, db)
    row = find_row(draft, offer_id)
    if not is_deleted(draft, row):
        raise HTTPException(422, "Скрыть из списка можно только удалённый оффер")
    row.dismissed = True
    db.commit()
    return view(draft)


@router.post("/{stream_id}/draft/offers/{offer_id}/undismiss")
async def undismiss_offer(stream_id: int, offer_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    find_row(draft, offer_id).dismissed = False
    db.commit()
    return view(draft)


@router.post("/{stream_id}/draft/offers/{offer_id}/hide")
async def hide_offer(stream_id: int, offer_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    find_row(draft, offer_id).hidden = True
    return save(draft, db)


@router.post("/{stream_id}/draft/offers/{offer_id}/show")
async def show_offer(stream_id: int, offer_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    find_row(draft, offer_id).hidden = False
    return save(draft, db)


@router.put("/{stream_id}/draft/offers/{offer_id}/pin")
async def pin_offer(
    stream_id: int, offer_id: int, body: PinBody, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)
):
    draft = await existing_draft(stream_id, k, db)
    row = find_row(draft, offer_id)
    if not is_visible(row):
        raise HTTPException(422, "Hidden or removed offers cannot be pinned")
    others = pinned_total(draft.offers, exclude_offer_id=offer_id)
    if others + body.share > 100:
        raise HTTPException(422, f"Закреплено уже {others}%, на этот оффер можно не больше {100 - others}%")
    row.pinned, row.share = True, body.share
    return save(draft, db)


@router.delete("/{stream_id}/draft/offers/{offer_id}/pin")
async def unpin_offer(stream_id: int, offer_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    find_row(draft, offer_id).pinned = False
    return save(draft, db)


@router.post("/{stream_id}/draft/rebalance")
async def rebalance(stream_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    """Unpin everything and split evenly."""
    draft = await existing_draft(stream_id, k, db)
    for r in draft.offers:
        r.pinned = False
    return save(draft, db)


@router.post("/{stream_id}/draft/discard")
async def discard(stream_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    """Throw away local edits and reload from Keitaro (committed pins and deleted offers are kept)."""
    draft = await existing_draft(stream_id, k, db)
    _, current, current_ordered = await fetch_offers(k, stream_id)
    pins = base_pins(draft)
    draft.base = with_pins(current, pins)
    fill_rows(draft, current_ordered, keep_pins=pins)
    db.commit()
    return view(draft)


@router.post("/{stream_id}/draft/commit")
async def commit(stream_id: int, k: KeitaroClient = Depends(get_keitaro), db: Session = Depends(get_db)):
    draft = await existing_draft(stream_id, k, db)
    v = view(draft)
    if not v["dirty"]:
        raise HTTPException(409, "Нет изменений для применения")
    if v["problems"]:
        raise HTTPException(422, "; ".join(v["problems"]))

    before = normalize(draft.base)
    if not v["keitaro_dirty"]:  # only pins changed — nothing to send
        draft.base = with_pins(before, {r.offer_id: r.pinned for r in draft.offers})
        db.commit()
        return view(draft)

    offers = payload(draft)
    updated = await k.update_stream(stream_id, {"offers": offers})
    after = normalize(updated.get("offers"))

    b_map = {o["offer_id"]: o for o in before}
    a_map = {o["offer_id"]: o for o in after}
    changed = sorted(oid for oid in b_map.keys() | a_map.keys() if b_map.get(oid) != a_map.get(oid))
    cid = draft.keitaro_campaign_id
    local = db.scalar(select(Campaign).where(Campaign.keitaro_id == cid)) if cid else None
    db.add(
        OfferChange(
            campaign_id=local.id if local else None,
            keitaro_campaign_id=cid,
            stream_id=stream_id,
            action="commit",
            offer_ids=changed,
            shares_before={str(o["offer_id"]): o["share"] for o in before},
            shares_after={str(o["offer_id"]): o["share"] for o in after},
            offers_before=before,
            offers_after=after,
        )
    )
    now = datetime.now()
    for r in draft.offers:
        if r.removed:
            r.deleted_at = r.deleted_at or now  # keep the row: deleted, but can be returned
            r.pinned = False
        else:
            r.deleted_at = None
    draft.base = with_pins(after, {r.offer_id: r.pinned for r in draft.offers if not r.removed})
    db.commit()
    return view(draft)


# ---------- history ----------
def _version(change: OfferChange, which: str) -> list[dict]:
    full = change.offers_before if which == "before" else change.offers_after
    if full is not None:
        return full
    shares = change.shares_before if which == "before" else change.shares_after  # legacy rows
    return [{"offer_id": int(k), "share": v, "state": "active"} for k, v in shares.items()]


@router.get("/{stream_id}/history")
async def history(stream_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(OfferChange).where(OfferChange.stream_id == stream_id).order_by(OfferChange.id.desc()))
    return [
        {
            "id": c.id,
            "action": c.action,
            "created_at": c.created_at.isoformat(sep=" ", timespec="seconds") if c.created_at else None,
            "offer_ids": c.offer_ids,
            "before": _version(c, "before"),
            "after": _version(c, "after"),
        }
        for c in rows
    ]


@router.post("/{stream_id}/history/{change_id}/load")
async def load_version(
    stream_id: int,
    change_id: int,
    body: LoadVersion,
    k: KeitaroClient = Depends(get_keitaro),
    db: Session = Depends(get_db),
):
    """Put a past version into the draft (not committed — review and commit it yourself)."""
    change = db.get(OfferChange, change_id)
    if change is None or change.stream_id != stream_id:
        raise HTTPException(404, "Version not found")
    draft = await existing_draft(stream_id, k, db)
    target = {o["offer_id"]: o for o in _version(change, body.which)}
    have = {r.offer_id: r for r in draft.offers}
    base_ids = {o["offer_id"] for o in draft.base}

    for oid, r in list(have.items()):
        if oid in target:
            r.removed = r.dismissed = False
            r.hidden = target[oid]["state"] != "active"
            r.share, r.pinned = target[oid]["share"], True
        elif oid in base_ids or r.deleted_at:
            r.removed = True
        else:
            draft.offers.remove(r)
    pos = max((r.position for r in draft.offers), default=-1)
    for oid, o in target.items():
        if oid not in have:
            pos += 1
            draft.offers.append(
                DraftOffer(offer_id=oid, share=o["share"], hidden=o["state"] != "active", pinned=True, position=pos)
            )
    # versions are restored exactly, so their shares are pinned; unpin to auto-balance again
    return save(draft, db)
