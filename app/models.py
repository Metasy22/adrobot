from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Campaign(Base):
    """Campaign created through this app (Keitaro is the source of truth; this is our history)."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    keitaro_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str] = mapped_column(String(255))
    country: Mapped[str] = mapped_column(String(8))
    offer_id: Mapped[int] = mapped_column(Integer)
    domain_id: Mapped[int | None] = mapped_column(Integer)
    group_id: Mapped[int | None] = mapped_column(Integer)
    traffic_source_id: Mapped[int | None] = mapped_column(Integer)
    geo_stream_id: Mapped[int | None] = mapped_column(Integer)
    offer_stream_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    changes: Mapped[list["OfferChange"]] = relationship(back_populates="campaign")


class OfferChange(Base):
    """Audit log of offer add/remove/reweight operations on a stream."""

    __tablename__ = "offer_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("campaigns.id"))
    keitaro_campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    stream_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(16))  # add | remove | set_shares
    offer_ids: Mapped[list] = mapped_column(JSON)
    shares_before: Mapped[dict] = mapped_column(JSON)
    shares_after: Mapped[dict] = mapped_column(JSON)
    # full Keitaro offer lists [{offer_id, share, state}] so any version can be restored
    offers_before: Mapped[list | None] = mapped_column(JSON)
    offers_after: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    campaign: Mapped[Campaign | None] = relationship(back_populates="changes")


class StreamDraft(Base):
    """Local working copy of a stream's offers. Edited freely, pushed to Keitaro on commit.

    `base` is the Keitaro offer list the draft was built on; draft != base means unsaved changes.
    """

    __tablename__ = "stream_drafts"

    stream_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    keitaro_campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    base: Mapped[list] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    offers: Mapped[list["DraftOffer"]] = relationship(
        back_populates="draft", cascade="all, delete-orphan", order_by="DraftOffer.position"
    )


class DraftOffer(Base):
    __tablename__ = "draft_offers"
    __table_args__ = (UniqueConstraint("stream_id", "offer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("stream_drafts.stream_id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int] = mapped_column(Integer)
    share: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)  # share is fixed, not rebalanced
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # sent to Keitaro as state=disabled, share 0
    removed: Mapped[bool] = mapped_column(Boolean, default=False)  # pending removal, restorable until commit
    # set when a removal is committed: the row stays so the offer can be returned later
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # hidden from the list
    position: Mapped[int] = mapped_column(Integer, default=0)

    draft: Mapped[StreamDraft] = relationship(back_populates="offers")
