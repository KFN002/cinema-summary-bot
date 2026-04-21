from app.services.search import MovieSearchService


def test_normalize_title():
    service = MovieSearchService()
    assert service.normalize_title("  The Matrix!!! ") == "the matrix"


def test_search_finds_close_match():
    service = MovieSearchService()
    results = service.search("Shuter Island")
    assert results
    assert results[0].title == "Shutter Island"
