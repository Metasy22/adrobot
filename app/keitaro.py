"""Thin async client over Keitaro Admin API v1 (see keitaro_admin_api_v1.yaml)."""

from typing import Any

import httpx

from app.config import settings


class KeitaroError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"Keitaro API error {status}: {detail}")
        self.status = status
        self.detail = detail


class KeitaroClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self._http = httpx.AsyncClient(
            base_url=(base_url or settings.keitaro_url).rstrip("/") + "/admin_api/v1",
            # The tracker sits behind a WAF that rejects default library user agents.
            headers={"Api-Key": api_key or settings.api_key, "User-Agent": "adrobot-client/0.1"},
            timeout=30,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        resp = await self._http.request(method, path, **kw)
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise KeitaroError(resp.status_code, detail)
        return resp.json() if resp.content else None

    # --- reference data ---
    async def domains(self) -> list[dict]:
        return await self._request("GET", "/domains")

    async def groups(self) -> list[dict]:
        return await self._request("GET", "/groups", params={"type": "campaigns"})

    async def traffic_sources(self) -> list[dict]:
        return await self._request("GET", "/traffic_sources")

    async def offers(self) -> list[dict]:
        return await self._request("GET", "/offers")

    async def offer(self, offer_id: int) -> dict:
        return await self._request("GET", f"/offers/{offer_id}")

    # --- campaigns ---
    async def campaigns(self) -> list[dict]:
        return await self._request("GET", "/campaigns")

    async def campaign(self, campaign_id: int) -> dict:
        return await self._request("GET", f"/campaigns/{campaign_id}")

    async def create_campaign(self, payload: dict) -> dict:
        return await self._request("POST", "/campaigns", json=payload)

    async def update_campaign(self, campaign_id: int, payload: dict) -> dict:
        return await self._request("PUT", f"/campaigns/{campaign_id}", json=payload)

    async def delete_campaign(self, campaign_id: int) -> Any:
        return await self._request("DELETE", f"/campaigns/{campaign_id}")

    async def campaign_streams(self, campaign_id: int) -> list[dict]:
        return await self._request("GET", f"/campaigns/{campaign_id}/streams")

    # --- streams ---
    async def stream(self, stream_id: int) -> dict:
        return await self._request("GET", f"/streams/{stream_id}")

    async def create_stream(self, payload: dict) -> dict:
        return await self._request("POST", "/streams", json=payload)

    async def update_stream(self, stream_id: int, payload: dict) -> dict:
        return await self._request("PUT", f"/streams/{stream_id}", json=payload)


async def get_keitaro():
    client = KeitaroClient()
    try:
        yield client
    finally:
        await client.aclose()
