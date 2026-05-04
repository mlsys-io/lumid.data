"""``/healthz`` smoke."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lumid_data.server.routers import health


def test_healthz() -> None:
    app = FastAPI()
    app.include_router(health.router)
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
