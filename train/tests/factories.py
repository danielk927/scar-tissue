"""Rows for the Person 3 tables, built with the shared models."""

from datetime import datetime, timezone

from scar_schemas.models import GenerationRecord, GenStatus, ModelVersion

NOW = datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc)


def model_version(version: str = "base", status: str = "promoted", **kw) -> ModelVersion:
    fields = dict(
        version=version,
        arm=None,
        seed=None,
        generation=0,
        parent_version=None,
        base_model_hf_id="Qwen/Example-4B",
        endpoint_base_url="http://models.local/v1",
        endpoint_model_id="base",
        adapter_uri=None,
        training_job_id=None,
        snapshot_id=None,
        status=status,
        created_at=NOW,
    )
    fields.update(kw)
    return ModelVersion(**fields)


def generation(arm: str, seed: int, g: int, status: GenStatus, current_model: str, current_prompt: str = "system_v1"):
    return GenerationRecord(
        generation_id=f"{arm}-s{seed}-g{g}",
        arm=arm,
        seed=seed,
        generation=g,
        status=status,
        parent_model_version="base",
        parent_prompt_version="system_v1",
        candidate_model_version=None,
        candidate_prompt_version=None,
        current_model_version=current_model,
        current_prompt_version=current_prompt,
        started_at=NOW,
    )
