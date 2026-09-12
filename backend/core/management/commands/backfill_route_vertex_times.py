"""Queue geometry refreshes for routes that predate persisted per-vertex timing."""

from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from core.models import RecurringRoute
from core.tasks import refresh_route_geometry
from core.wind import valid_vertex_times


class Command(BaseCommand):
    help = "Preview routes with missing/invalid vertex times; --enqueue queues their geometry refresh"

    def add_arguments(self, parser):
        parser.add_argument("--enqueue", action="store_true")
        parser.add_argument("--route-id", type=UUID)
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit <= 0:
            raise CommandError("--limit must be positive")
        routes = RecurringRoute.objects.only("id", "polyline", "vertex_times").order_by("id")
        if options["route_id"]:
            routes = routes.filter(id=options["route_id"])
        matched = enqueued = 0
        for route in routes.iterator(chunk_size=100):
            if valid_vertex_times(route.polyline_coordinates, route.vertex_times):
                continue
            matched += 1
            if options["enqueue"]:
                refresh_route_geometry.enqueue(str(route.id), backfill_only=True)
                enqueued += 1
            if limit is not None and matched >= limit:
                break
        self.stdout.write(
            f"Matched: {matched}; enqueued: {enqueued}" + (" (dry run)" if not options["enqueue"] else "")
        )
