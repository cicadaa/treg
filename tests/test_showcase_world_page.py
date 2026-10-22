"""The showcase world: people placed on landscapes by a hand-kept markdown table, upvoted by signed-in
people. Every row must land somewhere, keep a lasting id and show its picture; a vote may name only
a person in the table; the paintings need no library; and, like /showcase, all of it exists only
where treg.to serves it."""

from __future__ import annotations

import re

from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from treg.api import app
from treg.application import showcase
from treg.config import get_settings
from treg.domain import showcase as rules
from treg.infra.db import session_maker
from treg.models import ShowcasePersonVote

PATH = "/gtm-leaderboard"


async def test_world_reads_its_table_and_votes_and_draws_without_a_library(clients: AsyncClient):
    r = await clients.get(PATH)
    assert r.status_code == 200
    html = r.text
    assert "{BASE}" not in html
    assert 'fetch("/media/showcase/world.md"' in html  # the hand-kept table is who stands where
    assert '"/gtm-leaderboard/votes"' in html and '"/gtm-leaderboard/vote"' in html
    # the paintings are inline SVG: no third-party script is needed to see them
    assert not re.findall(r'<script[^>]+src="https?://', html)
    for key in rules.WORLD_LANDSCAPES:
        assert f"{key}: () =>" in html, key  # every landscape has its painting
    assert 'href="/showcase#' in html  # a way back to the gallery
    assert f'href="{PATH}"' in (await clients.get("/showcase")).text


async def test_every_row_of_the_table_lands_keeps_an_id_and_shows(clients: AsyncClient):
    r = await clients.get("/media/showcase/world.md")
    assert r.status_code == 200
    raw = [line for line in r.text.splitlines() if line.strip().startswith("|")]
    people = rules.world_people(r.text)
    # a misspelt landscape or a bad id drops a row without a word, and its votes with it
    assert len(people) == len(raw) - 2, "every row must parse"
    assert people, "an empty table leaves every summit bare"
    ids = [p["id"] for p in people]
    assert len(ids) == len(set(ids)), "two rows with one id would share their upvotes"
    for p in people:
        if p["photo"]:
            assert (await clients.get("/media/showcase/" + p["photo"])).status_code == 200, p["name"]


def test_the_parser_drops_rows_that_could_not_be_placed_or_voted_on():
    md = "| id | name | landscape |\n|---|---|---|\n| ada | Ada | Volcano |\n| Bad Id | Bo | creek |\n| cy | Cy | moon |\n"
    assert [(p["id"], p["landscape"]) for p in rules.world_people(md)] == [("ada", "volcano")]


async def test_a_person_takes_one_upvote_per_browser_and_per_address_with_no_sign_in(clients: AsyncClient):
    """No sign-in: the voter cookie names a browser and the address hash names a network, and a
    person takes one upvote from each, so clearing cookies does not buy a second vote."""
    person = sorted(showcase.world_ids())[0]
    clients.headers.pop("X-Treg-Token", None)
    first = await clients.get("/gtm-leaderboard/votes")
    assert first.json() == {"counts": {}, "upvoted": []}
    assert "treg_voter" in first.cookies  # a voter id is handed out before the first vote
    for _ in range(2):  # a second upvote changes nothing
        r = await clients.post("/gtm-leaderboard/vote", json={"id": person, "up": True})
        assert r.json() == {"id": person, "up": True, "upvotes": 1}
    assert (await clients.get("/gtm-leaderboard/votes")).json() == {"counts": {person: 1}, "upvoted": [person]}

    # a fresh browser on the same address is not counted again
    clients.cookies.clear()
    again = (await clients.post("/gtm-leaderboard/vote", json={"id": person, "up": True})).json()
    assert again == {"id": person, "up": False, "upvotes": 1}

    async with session_maker() as db:
        row = (await db.execute(select(ShowcasePersonVote))).scalar_one()
    assert "127.0.0.1" not in row.address_hash and row.address_hash == showcase._address_hash("127.0.0.1")

    # someone not in the table cannot collect votes, whatever id is sent
    assert (await clients.post("/gtm-leaderboard/vote", json={"id": "not-in-the-table", "up": True})).status_code == 404
    assert (await clients.post("/gtm-leaderboard/vote", json={"id": person, "up": True},
                               headers={"Origin": "https://evil.example"})).status_code == 403


async def test_only_the_browser_that_upvoted_can_take_it_back(clients: AsyncClient):
    person = sorted(showcase.world_ids())[0]
    clients.headers.pop("X-Treg-Token", None)
    await clients.post("/gtm-leaderboard/vote", json={"id": person, "up": True})
    mine = clients.cookies.get("treg_voter")
    clients.cookies.clear()
    assert (await clients.post("/gtm-leaderboard/vote", json={"id": person, "up": False})).json()["upvotes"] == 1
    clients.cookies.set("treg_voter", mine)
    assert (await clients.post("/gtm-leaderboard/vote", json={"id": person, "up": False})).json() == \
        {"id": person, "up": False, "upvotes": 0}
    async with session_maker() as db:
        assert (await db.execute(select(ShowcasePersonVote))).scalars().all() == []


async def test_world_404s_on_a_self_hosted_registry(monkeypatch):
    monkeypatch.setenv("TREG_PUBLIC_URL", "https://registry.example.internal")
    get_settings.cache_clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://registry") as c:
            assert (await c.get(PATH)).status_code == 404
            assert (await c.get("/gtm-leaderboard/votes")).status_code == 404
    finally:
        get_settings.cache_clear()
