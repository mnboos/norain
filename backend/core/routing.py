"""WebSocket URL routing."""

from django.urls import path

from .consumers import ForecastJobConsumer

websocket_urlpatterns = [
    path("ws/forecast/<uuid:job_id>/", ForecastJobConsumer.as_asgi()),
]
