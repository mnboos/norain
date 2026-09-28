"""Browser push delivery (VAPID). Briefings and the test button both send through here."""

import json

from django.conf import settings
from pywebpush import WebPushException, webpush

from core import telemetry


def push_configured():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY and settings.VAPID_SUBJECT)


def send_push(devices, title, body, url, tag):
    """Send to each device; True when at least one push service accepted it.

    A 404/410 means the browser dropped the subscription, so the row goes too.
    """
    if not push_configured():
        return False
    sent = False
    for device in devices:
        try:
            webpush(
                subscription_info={"endpoint": device.endpoint, "keys": device.keys},
                data=json.dumps({"title": title, "body": body, "url": url, "tag": tag}),
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": settings.VAPID_SUBJECT},
                ttl=600,
                timeout=10,
            )
            sent = True
        except WebPushException as exc:
            if exc.response is not None and exc.response.status_code in {404, 410}:
                device.delete()
            telemetry.event("briefing.delivery", channel="push", outcome="failed")
    return sent
