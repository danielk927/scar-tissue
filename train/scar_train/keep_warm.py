"""Keep the current D-s0 endpoint warm for a bounded demo window."""

import argparse
import os
import sys
import time
from collections.abc import Callable

import httpx
from pydantic import TypeAdapter
from scar_schemas.models import ModelVersion

from .campaign import CampaignError, Services
from .checks import ToolCallCheckError, tool_call_check
from .config import get_settings


def keep_warm(
    services: Services,
    *,
    api_key: str,
    minutes: float,
    interval_seconds: float = 120,
    check: Callable = tool_call_check,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    echo: Callable[[str], None] = print,
) -> tuple[int, int]:
    deadline = clock() + minutes * 60
    checks, failures = 0, 0
    while clock() < deadline:
        checks += 1
        try:
            current = services.call("GET", "/models/current?arm=D&seed=0")
            if not isinstance(current, dict) or "model_version" not in current:
                raise CampaignError("the model registry returned no current model")
            models = TypeAdapter(list[ModelVersion]).validate_python(services.call("GET", "/models"))
            model = next((model for model in models if model.version == current["model_version"]), None)
            if model is None or model.status != "promoted":
                raise CampaignError("current D-s0 model is not registered as promoted")
            check(model.endpoint_base_url, model.endpoint_model_id, api_key)
            echo(f"warm: {model.version} ({model.endpoint_model_id})")
        except (CampaignError, ToolCallCheckError, ValueError) as error:
            failures += 1
            echo(f"warm check failed: {type(error).__name__}; verify the service and wake the deployment if parked")
        next_check = min(clock() + interval_seconds, deadline)
        while clock() < next_check:
            sleep(min(60, next_check - clock()))
    return checks, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=120)
    parser.add_argument("--interval-seconds", type=float, default=120)
    args = parser.parse_args(argv)
    if args.minutes <= 0 or not 0 < args.interval_seconds <= 240:
        parser.error("minutes must be positive and interval-seconds must be in (0, 240]")
    settings = get_settings()
    if not settings.model_api_key:
        print("MODEL_API_KEY is required; configure the experiment host's .env", file=sys.stderr)
        return 1
    try:
        with httpx.Client(timeout=30) as http:
            services = Services(
                os.environ.get("TRAIN_SERVICE_URL", "http://localhost:8003"),
                settings.env_service_url,
                settings.agent_service_url,
                http=http,
            )
            checks, failures = keep_warm(
                services, api_key=settings.model_api_key, minutes=args.minutes, interval_seconds=args.interval_seconds
            )
        print(f"keep-warm finished: {checks} checks, {failures} failures; no more keep-alive requests will be sent")
        return int(checks == failures)
    except KeyboardInterrupt:
        print("keep-warm stopped; no more keep-alive requests will be sent")
        return 130


if __name__ == "__main__":
    sys.exit(main())
