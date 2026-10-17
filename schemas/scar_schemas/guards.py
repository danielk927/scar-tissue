# scar_schemas/guards.py - SHARED CONTRACT (SPEC 9.11)
def assert_train_only(task_ids: list[str], conn) -> None:
    rows = conn.execute(
        "SELECT task_id FROM app.tasks WHERE task_id = ANY(%s) AND split <> 'train'", (task_ids,)
    ).fetchall()
    if rows:
        raise RuntimeError(f"eval leakage: {len(rows)} non-train tasks, e.g. {rows[0][0]}")
