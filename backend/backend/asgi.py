"""
ASGI config for backend project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

# Matches manage.py and wsgi.py. Deployments must set DJANGO_SETTINGS_MODULE themselves --
# docker-compose.prod.yml does -- or they boot with DEBUG=True and CORS open to localhost.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings.development")

django_asgi_application = get_asgi_application()

# Imported after get_asgi_application(): loading consumers pulls in models, which requires
# the app registry to be populated.
from core.routing import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": django_asgi_application,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
