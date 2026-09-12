"""Enqueue the hourly pre-warm pass.

Usage: python manage.py refresh_forecasts

The command only queues work: `refresh_upcoming_forecasts` runs on a worker and fans out
to one `scan_route_forecasts` task per route, each of which enqueues the grid cells its
route still needs. Nothing here talks to a weather provider, so the command returns at
once and a worker must be running for anything to happen.
"""

from django.core.management.base import BaseCommand

from core.tasks import refresh_upcoming_forecasts


class Command(BaseCommand):
    help = "Queue the pre-warm pass for all upcoming route departures"

    def handle(self, *args, **options):
        result = refresh_upcoming_forecasts.enqueue()
        self.stdout.write(self.style.SUCCESS(f"Queued pre-warm pass (task {result.id})"))
