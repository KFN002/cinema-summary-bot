import pytest

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
