"""JSON-line logging to stdout (SPEC 7.4). Never log secrets."""

import json
import logging
import sys
from datetime import datetime, timezone

from .config import SERVICE_NAME

CONTEXT_KEYS = ("run_id", "task_id", "arm", "seed", "generation", "model_version")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": SERVICE_NAME,
            "msg": record.getMessage(),
        }
        for key in CONTEXT_KEYS:
            if hasattr(record, key):
                line[key] = getattr(record, key)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


logger = logging.getLogger("scar_agent")
