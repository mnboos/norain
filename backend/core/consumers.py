"""WebSocket delivery of forecast-job progress.

Cell fetches run on worker containers, so a finished cell cannot touch a socket directly.
Workers publish to the channel layer (see ``core/jobs.py``) and this consumer, living in
the daphne process, relays each event to the browsers watching that job.
"""

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .jobs import group_name, job_snapshot
from .models import ForecastJob


class ForecastJobConsumer(AsyncJsonWebsocketConsumer):
    """Streams one job's progress until it reaches a terminal state."""

    async def connect(self):
        self.job_id = self.scope["url_route"]["kwargs"]["job_id"]

        job = await self._load_job()
        if job is None:
            await self.close(code=4004)
            return

        await self.channel_layer.group_add(group_name(self.job_id), self.channel_name)
        await self.accept()

        # Send the current state straight away. Without this a job that finished between
        # the HTTP response and the socket opening -- which every fully-warm job does --
        # would never notify anyone, because its only event was published to an empty group.
        await self.send_json(job_snapshot(job))

    async def disconnect(self, code):
        if hasattr(self, "job_id"):
            await self.channel_layer.group_discard(group_name(self.job_id), self.channel_name)

    async def forecast_event(self, event):
        """Channel-layer handler for messages of type ``forecast.event``."""
        await self.send_json(event["payload"])

    @database_sync_to_async
    def _load_job(self) -> ForecastJob | None:
        """The job, if this connection is allowed to watch it.

        An ad-hoc job is guarded by its unguessable id alone; a saved route's forecast is
        private to its owner, matching the job endpoint in ``core/api/route_weather.py``.
        """
        job = ForecastJob.objects.filter(id=self.job_id).first()
        if job is None:
            return None
        if job.owner_id is not None:
            user = self.scope.get("user")
            if user is None or not user.is_authenticated or user.id != job.owner_id:
                return None
        return job
