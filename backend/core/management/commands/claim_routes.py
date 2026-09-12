"""Assign ownerless routes to one user.

Usage: python manage.py claim_routes --identifier you@example.test [--dry-run]

`RecurringRoute.owner` is nullable, so a route created before accounts existed has
owner=None. `list_routes()` filters by owner, which makes those routes invisible in the UI
without deleting them. On a fresh database this is a no-op.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from core.models import RecurringRoute


class Command(BaseCommand):
    help = "Assign every route without an owner to the given user"

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email or username to assign the routes to")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")

    def handle(self, *args, **options):
        value = options["identifier"].strip().lower()
        User = get_user_model()
        try:
            user = User.objects.get(Q(email__iexact=value) | Q(username__iexact=value))
        except User.DoesNotExist:
            raise CommandError(f"No user matching {value!r}.") from None

        orphans = RecurringRoute.objects.filter(owner__isnull=True)
        names = list(orphans.values_list("name", flat=True))
        if not names:
            self.stdout.write("No ownerless routes — nothing to do.")
            return

        if options["dry_run"]:
            self.stdout.write(f"Would assign {len(names)} route(s) to {user.email}: {', '.join(names)}")
            return

        count = orphans.update(owner=user)
        self.stdout.write(self.style.SUCCESS(f"Assigned {count} route(s) to {user.email}: {', '.join(names)}"))
