import pytest

from app.services.sources.omdb_adapter import OMDbSourceAdapter
import app.services.sources.omdb_adapter as omdb_module


class DummyResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise omdb_module.httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=None,
                response=self,
            )


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
async def test_omdb_adapter_disables_provider_after_auth_failure(monkeypatch):
    adapter = OMDbSourceAdapter("bad-key")
    calls: list[int] = []
    monkeypatch.setattr(
        omdb_module.httpx,
        "AsyncClient",
        lambda *args, **kwargs: CountingAsyncClient(
            DummyResponse(401, {"Response": "False", "Error": "Invalid API key!"}),
            calls,
        ),
    )

    first = await adapter.fetch_movie_evidence("Trainspotting", None)
    second = await adapter.fetch_movie_evidence("Trainspotting", None)

    assert first == []
    assert second == []
    assert len(calls) == 1
