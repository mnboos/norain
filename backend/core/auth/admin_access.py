"""Who may open the system dashboard: the same people the admin lets in.

Deliberately not ``admin.site.has_permission``. The admin site only becomes an
``OTPAdminSite`` when ``backend/urls.py`` is imported, which daphne does on its first HTTP
request -- a WebSocket reaching a freshly started process first would find a plain
``AdminSite`` and skip the authenticator code. This check applies ``ADMIN_OTP`` itself.
"""

from django.conf import settings
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.models import Device


def has_system_access(user, session) -> bool:
    """Active staff, and -- unless ``ADMIN_OTP`` is off -- verified by an authenticator code
    in this session, exactly as ``OTPMiddleware`` would have found it."""
    if user is None or not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if not settings.ADMIN_OTP:
        return True
    persistent_id = session.get(DEVICE_ID_SESSION_KEY) if session is not None else None
    device = Device.from_persistent_id(persistent_id) if persistent_id else None
    return device is not None and device.user_id == user.pk
