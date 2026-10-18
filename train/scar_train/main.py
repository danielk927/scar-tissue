"""Served train app with logging and Sentry (SPEC 7.4, 8.8). Run: uv run uvicorn scar_train.main:app --port 8003

Kept out of app.py so tests that build apps with create_app() never initialize Sentry.
"""

import logging

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from .app import create_app
from .config import get_settings
from .log import SERVICE, setup_logging


def init_observability() -> None:
    setup_logging()
    settings = get_settings()
    if not settings.sentry_dsn:
        logging.getLogger("scar_train").warning("SENTRY_DSN not set; Sentry disabled")
        return
    # SPEC 8.8 init plus Sentry Logs forwarding, as in Person 2's docs/SENTRY.md section 6.
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=1.0,
        send_default_pii=True,
        integrations=[LoggingIntegration(capture_sentry_logs=True, sentry_logs_level=logging.INFO)],
    )
    scope = sentry_sdk.get_global_scope()
    scope.set_tag("service", SERVICE)
    scope.set_attribute("service", SERVICE)


init_observability()
app = create_app()
