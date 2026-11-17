from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2, e.g. DE")
    offer_id: int
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None

    @field_validator("country")
    @classmethod
    def upper_country(cls, v: str) -> str:
        if not v.isalpha():
            raise ValueError("country must be a 2-letter code")
        return v.upper()


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None
    state: Literal["active", "disabled"] | None = None


class CampaignCreated(BaseModel):
    id: int
    alias: str
    name: str
    url: str | None
    geo_stream_id: int
    offer_stream_id: int
    campaign: dict
    streams: list[dict]


class OffersAdd(BaseModel):
    offer_ids: list[int] = Field(min_length=1)


class SharesSet(BaseModel):
    shares: dict[int, int] = Field(description="offer_id -> percent; must sum to 100")


class StreamOffers(BaseModel):
    stream_id: int
    offers: list[dict]
    total_share: int
