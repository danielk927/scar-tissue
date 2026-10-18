from datetime import timedelta

import pytest
from psycopg.errors import UniqueViolation
from scar_schemas.models import GenStatus

from scar_train import registry

from .factories import NOW, generation, model_version


def test_model_versions_round_trip_and_status_moves(conn):
    registry.put_model_version(conn, model_version())
    cand = model_version(
        "D-s0-g1",
        status="candidate",
        arm="D",
        seed=0,
        generation=1,
        parent_version="base",
        created_at=NOW + timedelta(hours=1),
    )
    registry.put_model_version(conn, cand)
    assert registry.get_model_version(conn, "D-s0-g1") == cand
    assert [m.version for m in registry.list_model_versions(conn)] == ["base", "D-s0-g1"]
    promoted = registry.set_model_status(conn, "D-s0-g1", "promoted")
    assert promoted.status == "promoted" and registry.get_model_version(conn, "D-s0-g1") == promoted
    assert conn.execute("SELECT status FROM app.model_versions WHERE version = 'D-s0-g1'").fetchone() == ("promoted",)
    with pytest.raises(UniqueViolation):
        registry.put_model_version(conn, cand)
    with pytest.raises(KeyError):
        registry.set_model_status(conn, "nope", "promoted")


def test_generations_upsert_and_filter(conn):
    rec = generation("D", 0, 1, GenStatus.collecting, "base")
    registry.put_generation(conn, rec)
    rec = rec.model_copy(update={"status": GenStatus.mining, "traffic_runs": 200})
    registry.put_generation(conn, rec)
    assert registry.get_generation(conn, "D-s0-g1") == rec
    assert conn.execute("SELECT status FROM app.generations").fetchall() == [("mining",)]
    registry.put_generation(conn, generation("C", 0, 1, GenStatus.collecting, "base"))
    registry.put_generation(conn, generation("D", 1, 1, GenStatus.collecting, "base"))
    assert [g.generation_id for g in registry.list_generations(conn)] == ["C-s0-g1", "D-s0-g1", "D-s1-g1"]
    assert [g.generation_id for g in registry.list_generations(conn, "D", 0)] == ["D-s0-g1"]
    assert registry.get_generation(conn, "D-s9-g9") is None


def test_current_uses_latest_finished_generation_else_base(conn):
    assert registry.current(conn, "D", 0) is None
    registry.put_model_version(conn, model_version())
    assert registry.current(conn, "D", 0) == ("base", "system_v1")
    registry.put_generation(conn, generation("D", 0, 1, GenStatus.promoted, "D-s0-g1"))
    registry.put_generation(conn, generation("D", 0, 2, GenStatus.rejected, "D-s0-g1"))
    registry.put_generation(conn, generation("D", 0, 3, GenStatus.training, "D-s0-g1"))
    assert registry.current(conn, "D", 0) == ("D-s0-g1", "system_v1")
    registry.put_generation(conn, generation("B", 0, 1, GenStatus.promoted, "base", "B-s0-g1"))
    assert registry.current(conn, "B", 0) == ("base", "B-s0-g1")
    assert registry.current(conn, "D", 1) == ("base", "system_v1")
