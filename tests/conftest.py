# SPDX-License-Identifier: MIT
import copy
from pathlib import Path
import pytest
from wiki_stats.wiki_client import FixtureClient
from wiki_stats.wikitext_parser import WikitextParser


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client():
    return FixtureClient(FIXTURES)


@pytest.fixture
def snapshot(client):
    return client.fetch_page("Кляйндинст, Тим")


@pytest.fixture
def package(snapshot):
    parser = WikitextParser(snapshot.text)
    row = parser.club_table().seasons[-1]
    return {
        "schema_version": "1.0", "package_id": "test-only", "generated_at": "2026-10-08T00:00:00Z",
        "articles": [{"title": snapshot.title, "player": "Тим Кляйндинст", "base_revid": snapshot.revid,
          "operations": [{"id": "season", "type": "update_stats",
            "entity": {"name": "Боруссия Мёнхенгладбах", "wikitext": row.club}, "season": row.season,
            "competition": {"name": "Чемпионат", "category": "Чемпионат", "scope": "category"},
            "target": {"structure": "club_table"}, "expected": {"appearances": 3, "goals": 0},
            "new": {"appearances": 4, "goals": 1}, "as_of": "2026-10-05",
            "sources": [{"url": "https://www.bundesliga.com/de/spieler/tim-kleindienst",
                         "provenance": "user_provided", "coverage_as_of": "2026-10-05",
                         "note": "Синтетические числа теста, не подтвержденная статистика"}]}]}]}


@pytest.fixture
def article(package):
    return copy.deepcopy(package["articles"][0])
