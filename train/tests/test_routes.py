from scar_schemas.models import GenStatus

from scar_train import registry

from .factories import generation, model_version


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_models_and_current(client, conn):
    r = client.get("/models/current", params={"arm": "D", "seed": 0})
    assert r.status_code == 503 and r.json()["error"] == "base_not_registered"
    registry.put_model_version(conn, model_version())
    assert [m["version"] for m in client.get("/models").json()] == ["base"]
    assert client.get("/models/current", params={"arm": "D", "seed": 0}).json() == {
        "model_version": "base",
        "prompt_version": "system_v1",
    }
    assert client.get("/models/current", params={"arm": "Z", "seed": 0}).status_code == 422


def test_generations_routes(client, conn):
    registry.put_generation(conn, generation("D", 0, 1, GenStatus.promoted, "D-s0-g1"))
    assert [g["generation_id"] for g in client.get("/generations", params={"arm": "D"}).json()] == ["D-s0-g1"]
    assert client.get("/generations/D-s0-g1").json()["current_model_version"] == "D-s0-g1"
    r = client.get("/generations/D-s0-g9")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
