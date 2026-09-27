"""WebSocket delivery of forecast-job progress and system-dashboard change notices.

Cell fetches run on worker containers, so a finished cell cannot touch a socket directly.
Workers publish to the channel layer (see ``core/jobs.py``, ``core/system_events.py``) and
these consumers, living in the daphne process, relay each event to the browsers watching.
"""

import asyncio
from time import monotonic

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .auth.admin_access import has_system_access
from .entitlements import entitlements_for_sync
from .jobs import group_name, job_snapshot, restrict_job_result
from .models import ForecastJob
from .system_events import SYSTEM_GROUP, TOPICS


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
        job = await self._load_job()
        if job is None:
            await self.close(code=4004)
            return
        payload = event["payload"] if event["payload"].get("status") != ForecastJob.Status.DONE else job_snapshot(job)
        await self.send_json(payload)

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
        restrict_job_result(job, entitlements_for_sync(self.scope.get("user")))
        return job


class SystemEventsConsumer(AsyncJsonWebsocketConsumer):
    """Tells an open system dashboard which of its panels to refetch.

    Frames carry topics only, never data: ``{"type": "hello" | "changed", "topics": [...]}``.
    A job's fan-out stores a cell and publishes progress on every settle, so each topic is
    throttled here -- sent at once, then at most once per window, the last change always
    delivered when the window ends. Without it the cell layer, which refetches every page,
    would be reloaded every second while a forecast runs.
    """

    # Seconds between two frames naming the same topic.
    THROTTLE = {"jobs": 1.0, "cells": 5.0, "routes": 5.0, "journeys": 5.0}

    async def connect(self):
        self.sent_at: dict[str, float] = {}
        self.pending: set[str] = set()
        self.flush: asyncio.Task | None = None
        # Accepted before the check: a close before accept() rejects the handshake, which a
        # browser only sees as 1006, and the page could not tell "refused" from "offline".
        await self.accept()
        if not await self._allowed():
            await self.close(code=4003)
            return
        await self.channel_layer.group_add(SYSTEM_GROUP, self.channel_name)
        # A reconnecting page missed whatever changed while it was away.
        await self.send_json({"type": "hello", "topics": list(TOPICS)})

    async def disconnect(self, code):
        await self.channel_layer.group_discard(SYSTEM_GROUP, self.channel_name)  # a no-op if refused
        if getattr(self, "flush", None) is not None:
            self.flush.cancel()

    async def system_event(self, event):
        """Channel-layer handler for messages of type ``system.event``."""
        now = monotonic()
        ready = []
        for topic in event["topics"]:
            if topic not in self.THROTTLE:
                continue
            if now - self.sent_at.get(topic, float("-inf")) >= self.THROTTLE[topic]:
                ready.append(topic)
                self.pending.discard(topic)
            else:
                self.pending.add(topic)
        if ready:
            await self._send(ready, now)
        if self.pending and (self.flush is None or self.flush.done()):
            self.flush = asyncio.create_task(self._flush_pending())

    async def _flush_pending(self):
        while self.pending:
            now = monotonic()
            wait = min(self.sent_at[t] + self.THROTTLE[t] for t in self.pending) - now
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            due = [t for t in self.pending if now - self.sent_at[t] >= self.THROTTLE[t]]
            self.pending.difference_update(due)
            await self._send(due, now)

    async def _send(self, topics, now):
        for topic in topics:
            self.sent_at[topic] = now
        await self.send_json({"type": "changed", "topics": sorted(set(topics))})

    @database_sync_to_async
    def _allowed(self) -> bool:
        return has_system_access(self.scope.get("user"), self.scope.get("session"))
