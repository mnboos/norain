"""Run forecast pre-warming once per hour in a dedicated container."""

import signal
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand


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
            call_command("refresh_forecasts")
            for _ in range(3600):
                if not running:
                    break
                time.sleep(1)
