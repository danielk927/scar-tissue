"""Register model version `base` (SPEC P3-04: generation 0, status promoted) after the tool-call check passes.

    uv run python -m scar_train.register_base --endpoint-base-url URL --endpoint-model-id ID [--base-model-hf-id ID]

The check uses MODEL_API_KEY; the HF id defaults to BASE_MODEL_HF_ID. Re-running with the same values only re-checks.
A registered endpoint never changes (the agent caches endpoints by version), so a different one is refused.
"""

import argparse
import sys
from collections.abc import Callable
from datetime import datetime, timezone

import psycopg
from scar_schemas.models import ModelVersion

from . import registry
from .checks import ToolCallCheckError, tool_call_check
from .config import get_settings


class RegisterError(RuntimeError):
    pass


def register_base(
    conn: psycopg.Connection,
    *,
    endpoint_base_url: str,
    endpoint_model_id: str,
    base_model_hf_id: str,
    api_key: str | None,
    check: Callable[[str, str, str | None], None] | None = None,
) -> ModelVersion:
    """Check the endpoint, then register `base`; returns the registered row (the existing one if identical)."""
    existing = registry.get_model_version(conn, registry.BASE_VERSION)
    wanted = (endpoint_base_url, endpoint_model_id, base_model_hf_id)
    if existing and (existing.endpoint_base_url, existing.endpoint_model_id, existing.base_model_hf_id) != wanted:
        raise RegisterError(
            f"base is already registered with endpoint {existing.endpoint_base_url} model"
            f" {existing.endpoint_model_id} ({existing.base_model_hf_id}); refusing to change it because the agent"
            " caches endpoints by model version"
        )
    (check or tool_call_check)(endpoint_base_url, endpoint_model_id, api_key)
    if existing:
        return existing
    mv = ModelVersion(
        version=registry.BASE_VERSION,
        arm=None,
        seed=None,
        generation=0,
        parent_version=None,
        base_model_hf_id=base_model_hf_id,
        endpoint_base_url=endpoint_base_url,
        endpoint_model_id=endpoint_model_id,
        adapter_uri=None,
        training_job_id=None,
        snapshot_id=None,
        status="promoted",
        created_at=datetime.now(timezone.utc),
    )
    registry.put_model_version(conn, mv)
    return mv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scar_train.register_base", description=__doc__.split("\n")[0])
    parser.add_argument("--endpoint-base-url", required=True, help="OpenAI-compatible base URL, e.g. http://host/v1")
    parser.add_argument("--endpoint-model-id", required=True, help="value sent as `model` in chat.completions")
    parser.add_argument("--base-model-hf-id", help="defaults to BASE_MODEL_HF_ID")
    args = parser.parse_args(argv)
    settings = get_settings()
    hf_id = args.base_model_hf_id or settings.base_model_hf_id
    if not hf_id:
        parser.error("pass --base-model-hf-id or set BASE_MODEL_HF_ID")
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        try:
            mv = register_base(
                conn,
                endpoint_base_url=args.endpoint_base_url,
                endpoint_model_id=args.endpoint_model_id,
                base_model_hf_id=hf_id,
                api_key=settings.model_api_key,
            )
        except (RegisterError, ToolCallCheckError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    print(mv.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
