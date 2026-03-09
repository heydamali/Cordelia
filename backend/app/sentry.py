import logging

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from app.config import settings

_initialized = False


def init_sentry() -> None:
    global _initialized
    if _initialized or not settings.SENTRY_DSN:
        return
    _initialized = True
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=0.1,
        integrations=[
            FastApiIntegration(),
            CeleryIntegration(),
            SqlalchemyIntegration(),
            LoggingIntegration(
                level=logging.WARNING,       # WARNING+ captured as breadcrumbs
                event_level=logging.ERROR,   # ERROR+ sent as Sentry events
            ),
        ],
        send_default_pii=False,
    )
