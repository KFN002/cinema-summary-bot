import json

import pytest

import app.services.search as search_module
from app.services.search import MovieSearchService


def test_normalize_title():
    service = MovieSearchService()
    assert service.normalize_title("  The Matrix!!! ") == "the matrix"


def test_split_title_and_year():
    service = MovieSearchService()
    assert service.split_title_and_year("Shutter Island 2010") == ("Shutter Island", 2010)
    assert service.split_title_and_year("The Matrix") == ("The Matrix", None)


def test_search_local_finds_close_match():
    service = MovieSearchService()
    results = service.search_local("Shuter Island")
    assert results
    assert results[0].title == "Shutter Island"


@pytest.mark.asyncio
async def test_search_uses_local_fallback_without_api_keys():
    service = MovieSearchService()
    results = await service.search("Inception 2010")
    assert results
    assert results[0].title == "Inception"


@pytest.mark.asyncio
async def test_search_finds_fight_club_from_local_fallback():
    service = MovieSearchService()
    results = await service.search("Fight Club")
    assert results
    assert results[0].title == "Fight Club"


@pytest.mark.asyncio
async def test_search_finds_se7en_alias():
    service = MovieSearchService()
    results = await service.search("Seven")
    assert results
    assert results[0].title == "Se7en"


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise search_module.httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=None,
                response=self,
            )


class BrokenJsonResponse(DummyResponse):
    def json(self):
        raise json.JSONDecodeError("Expecting value", "", 0)


class DummyAsyncClient:
    def __init__(self, response: DummyResponse, *args, **kwargs) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        return self.response


class CountingAsyncClient:
    def __init__(self, response: DummyResponse, calls: list[int], *args, **kwargs) -> None:
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        self.calls.append(1)
        return self.response


@pytest.mark.asyncio
async def test_search_omdb_invalid_api_key_returns_empty(monkeypatch):
    service = MovieSearchService()
    monkeypatch.setattr(
        search_module,
        "settings",
        type(
            "FakeSettings",
            (),
            {
                "has_omdb_api_key": staticmethod(lambda: True),
                "omdb_api_key": "bad-key",
                "has_tmdb_api_token": staticmethod(lambda: False),
                "tmdb_api_token": "PASTE_TMDB_API_TOKEN_HERE",
            },
        )(),
    )
    monkeypatch.setattr(
        search_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: DummyAsyncClient(
            DummyResponse(401, {"Response": "False", "Error": "Invalid API key!"}),
        ),
    )

    results = await service._search_omdb("Trainspotting", None)

    assert results == []


@pytest.mark.asyncio
async def test_search_omdb_disables_provider_after_auth_failure(monkeypatch):
    service = MovieSearchService()
    calls: list[int] = []
    monkeypatch.setattr(
        search_module,
        "settings",
        type(
            "FakeSettings",
            (),
            {
                "has_omdb_api_key": staticmethod(lambda: True),
                "omdb_api_key": "bad-key",
                "has_tmdb_api_token": staticmethod(lambda: False),
                "tmdb_api_token": "PASTE_TMDB_API_TOKEN_HERE",
            },
        )(),
    )
    monkeypatch.setattr(
        search_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: CountingAsyncClient(
            DummyResponse(401, {"Response": "False", "Error": "Invalid API key!"}),
            calls,
        ),
    )

    first = await service._search_omdb("Trainspotting", None)
    second = await service._search_omdb("Trainspotting", None)

    assert first == []
    assert second == []
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_search_wikipedia_403_falls_back_to_summary_candidates(monkeypatch):
    service = MovieSearchService()
    monkeypatch.setattr(
        search_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: DummyAsyncClient(DummyResponse(403, {"error": "forbidden"})),
    )

    async def fake_fetch_summary(title: str):
        if title == "Trainspotting (film)":
            return {
                "title": "Trainspotting (film)",
                "description": "1996 film by Danny Boyle",
                "titles": {"normalized": "Trainspotting (film)"},
            }
        return None

    monkeypatch.setattr(service, "_fetch_wikipedia_summary", fake_fetch_summary)

    results = await service._search_wikipedia_titles("Trainspotting", None)

    assert results
    assert results[0].title == "Trainspotting"


@pytest.mark.asyncio
async def test_search_wikipedia_invalid_json_falls_back_to_summary_candidates(monkeypatch):
    service = MovieSearchService()
    monkeypatch.setattr(
        search_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: DummyAsyncClient(BrokenJsonResponse(200, None)),
    )

    async def fake_fetch_summary(title: str):
        if title == "Trainspotting (film)":
            return {
                "title": "Trainspotting (film)",
                "description": "1996 film by Danny Boyle",
                "titles": {"normalized": "Trainspotting (film)"},
            }
        return None

    monkeypatch.setattr(service, "_fetch_wikipedia_summary", fake_fetch_summary)

    results = await service._search_wikipedia_titles("Trainspotting", None)

    assert results
    assert results[0].title == "Trainspotting"


@pytest.mark.asyncio
async def test_search_wikidata_finds_movie_candidates(monkeypatch):
    service = MovieSearchService()
    monkeypatch.setattr(
        search_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: DummyAsyncClient(
            DummyResponse(
                200,
                {
                    "search": [
                        {
                            "id": "Q838161",
                            "label": "Trainspotting",
                            "description": "1996 film by Danny Boyle",
                        }
                    ]
                },
            )
        ),
    )

    results = await service._search_wikidata_titles("Trainspotting", None)

    assert results
    assert results[0].title == "Trainspotting"
    assert results[0].year == 1996


@pytest.mark.asyncio
async def test_search_wikidata_403_returns_empty_without_exception(monkeypatch):
    service = MovieSearchService()
    monkeypatch.setattr(
        search_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: DummyAsyncClient(DummyResponse(403, {"error": "forbidden"})),
    )

    results = await service._search_wikidata_titles("Trainspotting", 1996)

    assert results == []


@pytest.mark.asyncio
async def test_search_uses_gigachat_fallback_when_other_sources_are_empty(monkeypatch):
    service = MovieSearchService()
    monkeypatch.setattr(
        search_module,
        "settings",
        type(
            "FakeSettings",
            (),
            {
                "has_omdb_api_key": staticmethod(lambda: False),
                "has_tmdb_api_token": staticmethod(lambda: False),
                "has_gigachat_credentials": staticmethod(lambda: True),
            },
        )(),
    )
    monkeypatch.setattr(service, "search_local", lambda query, top_k=5: [])

    async def empty_results(*args, **kwargs):
        return []

    async def gigachat_results(title: str, year: int | None):
        return [search_module.MovieCandidate(movie_id="llm:trainspotting:1996", title="Trainspotting", year=1996, confidence=0.91)]

    monkeypatch.setattr(service, "_search_omdb", empty_results)
    monkeypatch.setattr(service, "_search_tmdb", empty_results)
    monkeypatch.setattr(service, "_search_wikipedia_titles", empty_results)
    monkeypatch.setattr(service, "_search_wikidata_titles", empty_results)
    monkeypatch.setattr(service, "_search_with_gigachat", gigachat_results)

    results = await service.search("Trainspotting")

    assert results
    assert results[0].title == "Trainspotting"
    assert results[0].year == 1996


@pytest.mark.asyncio
async def test_search_uses_query_fallback_when_even_gigachat_returns_nothing(monkeypatch):
    service = MovieSearchService()
    monkeypatch.setattr(
        search_module,
        "settings",
        type(
            "FakeSettings",
            (),
            {
                "has_omdb_api_key": staticmethod(lambda: False),
                "has_tmdb_api_token": staticmethod(lambda: False),
                "has_gigachat_credentials": staticmethod(lambda: True),
            },
        )(),
    )
    monkeypatch.setattr(service, "search_local", lambda query, top_k=5: [])

    async def empty_results(*args, **kwargs):
        return []

    monkeypatch.setattr(service, "_search_omdb", empty_results)
    monkeypatch.setattr(service, "_search_tmdb", empty_results)
    monkeypatch.setattr(service, "_search_wikipedia_titles", empty_results)
    monkeypatch.setattr(service, "_search_wikidata_titles", empty_results)
    monkeypatch.setattr(service, "_search_with_gigachat", empty_results)

    results = await service.search("Trainspotting")

    assert results
    assert results[0].title == "Trainspotting"
    assert results[0].confidence == 0.58
