from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from . import (
            signals,  # noqa: F401 -- remove photo files with their rows
            tracing,  # noqa: F401 -- register task-envelope propagation
        )
        from .auth import signals as auth_signals  # noqa: F401 -- connect the allauth receivers
