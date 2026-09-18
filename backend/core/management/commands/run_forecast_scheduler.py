"""Run forecast pre-warming once per hour in a dedicated container."""

import signal
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.briefings import refresh_briefings
from core.telemetry import sample_queues


class Command(BaseCommand):
    help = "Run refresh_forecasts hourly until the process receives SIGTERM or SIGINT"

    def handle(self, *args, **options):
        running = True

        def stop_scheduler(signum, frame):
            nonlocal running
            running = False
            self.stdout.write(f"Received signal {signum}; stopping forecast scheduler.")

        signal.signal(signal.SIGTERM, stop_scheduler)
        signal.signal(signal.SIGINT, stop_scheduler)

        while running:
            try:
                call_command("refresh_forecasts")
            except Exception as exc:  # noqa: BLE001 -- queue monitoring must survive a failed enqueue
                self.stderr.write(f"Pre-warm enqueue failed: {type(exc).__name__}")
            for second in range(3600):
                if second % 60 == 0:
                    sample_queues()
                    try:
                        refresh_briefings.enqueue()
                    except Exception as exc:  # noqa: BLE001
                        self.stderr.write(f"Briefing enqueue failed: {type(exc).__name__}")
                if not running:
                    break
                time.sleep(1)
