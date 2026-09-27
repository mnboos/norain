"""WebSocket URL routing."""

from django.urls import path

from .consumers import ForecastJobConsumer, SystemEventsConsumer

websocket_urlpatterns = [
    path("ws/forecast/<uuid:job_id>/", ForecastJobConsumer.as_asgi()),
    path("ws/system/", SystemEventsConsumer.as_asgi()),
]
