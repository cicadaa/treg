"""A per_result row that declares `cost.reserve` holds what the caller can be billed, and one that
declares `cost.settle.count` settles the rows the provider returned, never above that hold."""

from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

from treg import api as A
from treg.config import get_settings
from treg.domain.catalog import store as catalog_store

from test_marketplace_call import _dropleads_response, _entries

REFDOMAINS = "seranking.web.linking_domains.list"
LOOKALIKE = "findymail.lookalike.search"


@pytest.fixture
def platform(monkeypatch):
    def enable(provider: str) -> None:
        monkeypatch.setenv(f"TREG_PLATFORM_KEY_{provider.upper()}", f"PLATFORM-{provider}")
        monkeypatch.setenv("TREG_PLATFORM_PROVIDERS", provider)
        get_settings.cache_clear()
    yield enable
    get_settings.cache_clear()


def _declare(monkeypatch, endpoint_id: str, **fields) -> None:
    cost = catalog_store.load().by_id[endpoint_id]["cost"]
    for key, value in fields.items():
        monkeypatch.setitem(cost, key, value)


async def _hold_and_charge(clients: AsyncClient, before: int) -> tuple[int, int]:
    entries = (await _entries(clients))[:-before or None]
    reserve = [-e["amount_micro"] for e in entries if e["kind"] == "reserve"]
    settle = [-e["amount_micro"] for e in entries if e["kind"] == "settle"]
    return reserve[0], settle[0]


@pytest.mark.parametrize(("query", "top", "returned", "hold", "charge"), [
    ({"limit": "300"}, 10_000, 250, 300 * 179, 250 * 179),
    ({"limit": "300"}, 10_000, 400, 300 * 179, 300 * 179),
    ({"limit": "99999"}, 1_000, 5, 1_000 * 179, 5 * 179),
    ({}, 10_000, 100, 100 * 179, 100 * 179),
])
async def test_declared_rows_hold_the_asked_limit_and_settle_the_returned_rows(
    clients: AsyncClient, monkeypatch, platform, query, top, returned, hold, charge,
):
    platform("seranking")
    _declare(monkeypatch, REFDOMAINS,
             reserve={"rows": {"param": "queryParams.limit",
                               "default": {"value": 100, "source": "observed"}, "max": top}},
             settle={"count": {"path": "refdomains"}})

    def upstream(request):
        return _dropleads_response(200, {"refdomains": [{"refdomain": f"d{i}.com"} for i in range(returned)]})

    async with AsyncClient(transport=httpx.MockTransport(upstream)) as vendor:
        monkeypatch.setattr(A.app.state, "http", vendor)
        before = len(await _entries(clients))
        r = await clients.get(f"/call/{REFDOMAINS}", params={"target": "example.com", "mode": "domain", **query})
    assert r.status_code == 200, r.text
    assert int(r.headers["X-Treg-Cost-Micro"]) == charge
    assert await _hold_and_charge(clients, before) == (hold, charge)


async def test_an_undeclared_entity_unit_row_keeps_its_one_entity_hold(
    clients: AsyncClient, monkeypatch, platform,
):
    platform("seranking")

    def upstream(request):
        return _dropleads_response(200, {"refdomains": [{"refdomain": f"d{i}.com"} for i in range(250)]})

    async with AsyncClient(transport=httpx.MockTransport(upstream)) as vendor:
        monkeypatch.setattr(A.app.state, "http", vendor)
        before = len(await _entries(clients))
        r = await clients.get(f"/call/{REFDOMAINS}", params={"target": "example.com", "limit": "300"})
    assert r.status_code == 200, r.text
    assert await _hold_and_charge(clients, before) == (179, 179)


@pytest.mark.parametrize(("limit", "hold"), [(15, 2 * 19_800), (20, 2 * 19_800), (21, 3 * 19_800)])
async def test_a_block_priced_row_holds_whole_blocks(
    clients: AsyncClient, monkeypatch, platform, limit, hold,
):
    platform("findymail")
    _declare(monkeypatch, LOOKALIKE,
             reserve={"rows": {"param": "body.limit",
                               "default": {"value": 10, "source": "docs"}, "max": 10_000}},
             reported_charge={"path": "credits_used", "unit": "credit"})
    credits = -(-limit // 10)

    def upstream(request):
        return _dropleads_response(200, {"companies": [{}] * limit, "credits_used": credits})

    async with AsyncClient(transport=httpx.MockTransport(upstream)) as vendor:
        monkeypatch.setattr(A.app.state, "http", vendor)
        before = len(await _entries(clients))
        r = await clients.post(f"/call/{LOOKALIKE}", json={"seed": "stripe.com", "limit": limit})
    assert r.status_code == 200, r.text
    assert await _hold_and_charge(clients, before) == (hold, credits * 19_800)
