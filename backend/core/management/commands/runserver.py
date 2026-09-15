"""Daphne's ASGI runserver, listening on BACKEND_PORT from the root .env by default.

Usage: python manage.py runserver            # 127.0.0.1:$BACKEND_PORT (8000 without it)
       python manage.py runserver 0.0.0.0:9000   # an explicit address still wins

Django's runserver has no setting for its port, so without this every launcher — the
justfile, a PyCharm run config — would have to repeat the port. `core` sits above `daphne` in
INSTALLED_APPS so this command is the one `manage.py` finds.
"""

import os

from daphne.management.commands.runserver import Command as DaphneRunserverCommand


class Command(DaphneRunserverCommand):
    default_port = os.environ.get("BACKEND_PORT") or "8000"
