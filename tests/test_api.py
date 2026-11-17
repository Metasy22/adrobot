"""API tests against an in-memory DB and a fake Keitaro (no network)."""

import copy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.keitaro import KeitaroError, get_keitaro
from app.main import app

STREAM = 500
KNOWN_OFFERS = {1, 2, 3, 4}


class FakeKeitaro:
    def __init__(self):
        self.next_id = 1000
        self.campaigns: dict[int, dict] = {}
        self.streams: dict[int, dict] = {
            STREAM: {
                "id": STREAM,
                "campaign_id": 77,
                "schema": "landings",
                "offers": [
                    {"offer_id": 1, "share": 50, "state": "active"},
                    {"offer_id": 2, "share": 50, "state": "active"},
                ],
            },
            501: {"id": 501, "campaign_id": 77, "schema": "redirect", "offers": []},
        }
        self.puts: list[dict] = []
        self.deleted: list[int] = []
        self.fail_stream_create = False

    def _id(self):
        self.next_id += 1
        return self.next_id

    async def stream(self, sid):
        if sid not in self.streams:
            raise KeitaroError(404, "not found")
        return copy.deepcopy(self.streams[sid])

    async def update_stream(self, sid, payload):
        self.puts.append(copy.deepcopy(payload))
        self.streams[sid]["offers"] = copy.deepcopy(payload["offers"])
        return copy.deepcopy(self.streams[sid])

    async def offer(self, oid):
        if oid not in KNOWN_OFFERS:
            raise KeitaroError(404, "not found")
        return {"id": oid}

    async def create_campaign(self, payload):
        c = {**payload, "id": self._id(), "domain": "https://example.test/"}
        self.campaigns[c["id"]] = c
        return c

    async def create_stream(self, payload):
        if self.fail_stream_create:
            raise KeitaroError(422, "bad stream")
        s = {**payload, "id": self._id()}
        self.streams[s["id"]] = s
        return s

    async def delete_campaign(self, cid):
        self.deleted.append(cid)


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    fake = FakeKeitaro()

    def _db():
        with Session() as s:
            yield s

    async def _k():
        yield fake

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_keitaro] = _k
    with TestClient(app) as client:
        yield client, fake
    app.dependency_overrides.clear()


def offers(resp):
    assert resp.status_code == 200, resp.text
    return {o["offer_id"]: (o["share"], o["status"]) for o in resp.json()["offers"]}


# ---------- campaign creator ----------
def test_create_campaign_builds_two_streams(env):
    client, fake = env
    r = client.post(
        "/api/campaigns",
        json={"name": "My Camp", "country": "de", "offer_id": 3, "domain_id": 1, "group_id": 2, "traffic_source_id": 3},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    camp = fake.campaigns[body["id"]]
    assert camp["domain_id"] == 1 and camp["group_id"] == 2 and camp["traffic_source_id"] == 3
    assert camp["alias"].startswith("my-camp-")

    geo = fake.streams[body["geo_stream_id"]]
    assert geo["schema"] == "redirect" and geo["action_type"] == "http"
    assert geo["action_payload"].startswith("https://google.")
    assert geo["filters"] == [{"name": "country", "mode": "accept", "payload": ["DE"]}]

    offer = fake.streams[body["offer_stream_id"]]
    assert offer["schema"] == "landings" and offer["type"] == "default"
    assert offer["offers"] == [{"offer_id": 3, "share": 100, "state": "active"}]


def test_create_campaign_rolls_back_on_stream_error(env):
    client, fake = env
    fake.fail_stream_create = True
    r = client.post(
        "/api/campaigns",
        json={"name": "x", "country": "DE", "offer_id": 1, "domain_id": 1, "group_id": 1, "traffic_source_id": 1},
    )
    assert r.status_code == 422
    assert fake.deleted == list(fake.campaigns)


def test_create_campaign_validates_country(env):
    client, _ = env
    r = client.post("/api/campaigns", json={"name": "x", "country": "D1", "offer_id": 1})
    assert r.status_code == 422


# ---------- offer editor ----------
def test_draft_edits_do_not_touch_keitaro_until_commit(env):
    client, fake = env
    assert offers(client.get(f"/api/streams/{STREAM}/draft")) == {1: (50, "synced"), 2: (50, "synced")}

    assert offers(client.post(f"/api/streams/{STREAM}/draft/offers", json={"offer_ids": [3]})) == {
        1: (34, "changed"), 2: (33, "changed"), 3: (33, "new"),
    }
    assert fake.puts == []

    r = client.post(f"/api/streams/{STREAM}/draft/commit")
    assert offers(r) == {1: (34, "synced"), 2: (33, "synced"), 3: (33, "synced")}
    assert fake.puts[-1]["offers"] == [
        {"offer_id": 1, "share": 34, "state": "active"},
        {"offer_id": 2, "share": 33, "state": "active"},
        {"offer_id": 3, "share": 33, "state": "active"},
    ]


def test_pin_keeps_share_and_splits_rest(env):
    client, _ = env
    client.post(f"/api/streams/{STREAM}/draft/offers", json={"offer_ids": [3, 4]})
    r = client.put(f"/api/streams/{STREAM}/draft/offers/1/pin", json={"share": 40})
    assert offers(r) == {1: (40, "changed"), 2: (20, "changed"), 3: (20, "new"), 4: (20, "new")}
    assert r.json()["total"] == 100

    too_much = client.put(f"/api/streams/{STREAM}/draft/offers/2/pin", json={"share": 70})
    assert too_much.status_code == 422


def test_removed_offer_stays_after_commit_and_can_be_hidden_and_returned(env):
    client, fake = env
    client.delete(f"/api/streams/{STREAM}/draft/offers/2")
    r = client.post(f"/api/streams/{STREAM}/draft/commit")
    assert offers(r) == {1: (100, "synced"), 2: (0, "deleted")}
    assert [o["offer_id"] for o in fake.streams[STREAM]["offers"]] == [1]

    # can't hide an offer that is still in the stream
    assert client.post(f"/api/streams/{STREAM}/draft/offers/1/dismiss").status_code == 422

    r = client.post(f"/api/streams/{STREAM}/draft/offers/2/dismiss")
    assert {o["offer_id"]: o["dismissed"] for o in r.json()["offers"]} == {1: False, 2: True}

    # discard keeps deleted rows
    r = client.post(f"/api/streams/{STREAM}/draft/discard")
    assert offers(r) == {1: (100, "synced"), 2: (0, "deleted")}

    r = client.post(f"/api/streams/{STREAM}/draft/offers/2/restore")
    assert offers(r) == {1: (50, "changed"), 2: (50, "new")}
    client.post(f"/api/streams/{STREAM}/draft/commit")
    assert {o["offer_id"]: o["share"] for o in fake.streams[STREAM]["offers"]} == {1: 50, 2: 50}


def test_removing_a_new_offer_just_drops_it(env):
    client, _ = env
    client.post(f"/api/streams/{STREAM}/draft/offers", json={"offer_ids": [3]})
    r = client.delete(f"/api/streams/{STREAM}/draft/offers/3")
    assert offers(r) == {1: (50, "synced"), 2: (50, "synced")}
    assert r.json()["dirty"] is False


def test_discard_and_history_rollback(env):
    client, fake = env
    client.post(f"/api/streams/{STREAM}/draft/offers", json={"offer_ids": [3]})
    assert offers(client.post(f"/api/streams/{STREAM}/draft/discard")) == {1: (50, "synced"), 2: (50, "synced")}

    client.put(f"/api/streams/{STREAM}/draft/offers/1/pin", json={"share": 80})
    client.post(f"/api/streams/{STREAM}/draft/commit")
    hist = client.get(f"/api/streams/{STREAM}/history").json()
    assert len(hist) == 1

    r = client.post(f"/api/streams/{STREAM}/history/{hist[0]['id']}/load", json={"which": "before"})
    assert offers(r) == {1: (50, "changed"), 2: (50, "changed")}
    client.post(f"/api/streams/{STREAM}/draft/commit")
    assert {o["offer_id"]: o["share"] for o in fake.streams[STREAM]["offers"]} == {1: 50, 2: 50}


def test_commit_refuses_bad_total_and_noop(env):
    client, _ = env
    assert client.post(f"/api/streams/{STREAM}/draft/commit").status_code == 409  # nothing changed
    client.put(f"/api/streams/{STREAM}/draft/offers/1/pin", json={"share": 30})
    client.put(f"/api/streams/{STREAM}/draft/offers/2/pin", json={"share": 30})
    r = client.get(f"/api/streams/{STREAM}/draft")
    assert r.json()["total"] == 60 and r.json()["can_commit"] is False
    assert client.post(f"/api/streams/{STREAM}/draft/commit").status_code == 422


def test_external_change_resyncs_clean_draft_and_flags_conflict_on_dirty(env):
    client, fake = env
    client.get(f"/api/streams/{STREAM}/draft")
    fake.streams[STREAM]["offers"] = [{"offer_id": 1, "share": 100, "state": "active"}]
    r = client.get(f"/api/streams/{STREAM}/draft")
    assert offers(r) == {1: (100, "synced")} and r.json()["conflict"] is False

    client.post(f"/api/streams/{STREAM}/draft/offers", json={"offer_ids": [3]})
    fake.streams[STREAM]["offers"] = [{"offer_id": 4, "share": 100, "state": "active"}]
    assert client.get(f"/api/streams/{STREAM}/draft").json()["conflict"] is True


def test_unknown_offer_and_wrong_stream(env):
    client, _ = env
    assert client.post(f"/api/streams/{STREAM}/draft/offers", json={"offer_ids": [999]}).status_code == 404
    assert client.get("/api/streams/501/draft").status_code == 422
