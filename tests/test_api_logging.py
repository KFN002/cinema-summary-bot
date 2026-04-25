import logging

from fastapi.testclient import TestClient

from app.main import app


def test_api_middleware_logs_requests_and_sets_request_id(caplog):
    with caplog.at_level(logging.INFO):
        with TestClient(app) as client:
            response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("api-")
    assert "api_request_started" in caplog.text
    assert "api_request_finished" in caplog.text
    assert response.headers["x-request-id"] in caplog.text
