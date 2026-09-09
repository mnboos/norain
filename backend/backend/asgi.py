"""
ASGI config for backend project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
from pathlib import Path

from django.core.asgi import get_asgi_application
from dotenv import load_dotenv

dotenv_path = Path(__file__).parent.parent.parent / ".env"
assert dotenv_path.is_file(), f"Dotenv file not found: {dotenv_path}"
load_dotenv(dotenv_path)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

application = get_asgi_application()
