"""Model signal receivers that are not allauth's (those live in core/auth/signals.py)."""

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import RoutePhoto


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
