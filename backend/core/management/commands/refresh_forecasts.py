"""Run the refresh_upcoming_forecasts task in-process.

Usage: python manage.py refresh_forecasts

Can be scheduled via cron or Claude Code's CronCreate to run hourly.
"""

from django.core.management.base import BaseCommand

from core.tasks import _refresh_upcoming_forecasts_async
import asyncio


class Command(BaseCommand):
    help = "Pre-fetch weather forecast grid cells for all upcoming route departures"

    def handle(self, *args, **options):

        result = asyncio.run(_refresh_upcoming_forecasts_async())
        self.stdout.write(
            self.style.SUCCESS(
                f"Refreshed {result['routes']} routes: "
                f"{result['cells_enqueued']} cell + {result['ensembles_enqueued']} ensemble refreshes enqueued"
            )
        )
