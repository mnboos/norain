"""
WSGI config for backend project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

from pathlib import Path

from dotenv import load_dotenv

dotenv_path = Path(__file__).parent.parent.parent / ".env"
assert dotenv_path.is_file()
load_dotenv(dotenv_path)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

application = get_wsgi_application()
