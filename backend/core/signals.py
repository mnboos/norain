"""Model signal receivers that are not allauth's (those live in core/auth/signals.py)."""

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from loguru import logger

from .models import CoverageArea, RoutePhoto
from .tasks import notify_area_covered


@receiver(post_delete, sender=RoutePhoto)
def delete_photo_files(sender, instance: RoutePhoto, **kwargs):
    """Remove a photo's files with its row, whether the photo, its route or its owner went.

    After commit, so a rolled-back delete never leaves a row pointing at nothing. A queryset
    or cascade delete sends this per row as well.
    """
    names = [(f.storage, f.name) for f in (instance.image, instance.thumbnail) if f.name]

    def remove():
        for storage, name in names:
            storage.delete(name)

    transaction.on_commit(remove)


@receiver(post_save, sender=CoverageArea)
def notify_coverage_subscribers(sender, instance: CoverageArea, **kwargs):
    """An area saved as covered mails everyone who asked about it, whoever saved it.

    Every save of a covered area enqueues the task; it only mails confirmed addresses that
    are still waiting and deletes each one it mailed, so a second save sends nothing twice.
    """
    if instance.status != CoverageArea.Status.COVERED:
        return
    code = instance.code

    def enqueue():
        try:
            notify_area_covered.enqueue(code)
        except Exception:  # noqa: BLE001 -- the admin's save worked; the next save retries
            logger.exception(f"Could not enqueue notify_area_covered for {code}")

    transaction.on_commit(enqueue)
