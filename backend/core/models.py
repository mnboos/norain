import uuid
from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.contrib.gis.db import models
from django.contrib.gis.geos import LineString, Point
from django.db.models.functions import Lower


def route_point(lat: float, lon: float) -> Point:
    """Build a WGS84 point while keeping the app-facing latitude-first API explicit."""
    return Point(float(lon), float(lat), srid=4326)


def route_line(coordinates: list[list[float]]) -> LineString:
    """Build a WGS84 line from GraphHopper's ``[[lon, lat], ...]`` coordinates."""
    return LineString(*(tuple(point[:2]) for point in coordinates), srid=4326)


class UserManager(DjangoUserManager):
    """Ensure accounts created through ``createsuperuser`` can sign in immediately."""

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("email_verified", True)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """A NoRain account: sign-in identity plus email-verification state.

    The reason this is a custom model rather than ``django.contrib.auth.User`` is the two
    constraints below. Django's default user permits duplicate and blank emails
    (``unique=False, blank=True``) and its ``username`` index is case-sensitive, so
    "one account per email address, however it is capitalised" cannot be expressed there
    — and constraints cannot be added to a model the project does not own.

    Both the email and the username are sign-in identities; see
    ``core.auth.backend.IdentityBackend``.
    """

    # Overridden from AbstractUser purely to make it required: an account with no email
    # could never verify itself or reset its password.
    email = models.EmailField("email address", blank=False)
    email_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = (
            models.UniqueConstraint(Lower("email"), name="core_user_email_ci_unique"),
            models.UniqueConstraint(Lower("username"), name="core_user_username_ci_unique"),
        )

    def __str__(self):
        return self.username or self.email


class Plan(models.TextChoices):
    """Billing tiers. Entitlements for each live in core/entitlements.py."""

    FREE = "free", "Free"
    PRO = "pro", "Pro"


class Subscription(models.Model):
    """What an account is entitled to, and the Stripe objects backing it.

    State is only ever written from verified Stripe webhook events — never from the
    Checkout success redirect, which a user can load, skip, or forge. An account with no
    row here is on the free tier.
    """

    # Statuses Stripe reports that still mean "this account has paid access".
    ACTIVE_STATUSES = ("active", "trialing")

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="subscription")
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.FREE)
    status = models.CharField(max_length=32, blank=True, default="", help_text="Stripe subscription status")

    stripe_customer_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email}: {self.plan} ({self.status or 'no status'})"


class ProcessedStripeEvent(models.Model):
    """Stripe redelivers events on any non-2xx, so each id is applied at most once."""

    event_id = models.CharField(max_length=255, primary_key=True)
    event_type = models.CharField(max_length=100, blank=True, default="")
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        return self.event_id


class RecurringRoute(models.Model):
    """A user-configured bike route with a cron schedule for recurring weather checks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="recurring_routes",
        help_text="User that owns this route. Routes without an owner are hidden from the app.",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")

    start_point = models.PointField(srid=4326, geography=True)
    start_name = models.CharField(max_length=300)

    destination_point = models.PointField(srid=4326, geography=True)
    dest_name = models.CharField(max_length=300)

    profile = models.CharField(
        max_length=50,
        default="bike",
        help_text="GraphHopper routing profile: bike, ebike, fast_ebike",
    )

    schedule_cron = models.CharField(max_length=100, help_text="5-field cron expression")
    schedule_description = models.CharField(max_length=200, help_text="Human-readable, e.g. 'Every Monday at 08:00'")

    # Pre-computed route geometry (populated by background task on create/update)
    polyline = models.LineStringField(
        srid=4326,
        geography=True,
        null=True,
        blank=True,
        help_text="Full GraphHopper route line in WGS84",
    )
    total_seconds = models.IntegerField(null=True, blank=True)
    total_distance_m = models.FloatField(null=True, blank=True)
    sample_points = models.JSONField(
        null=True,
        blank=True,
        help_text="[{lat, lon, lat_r, lon_r, elapsed_s, idx}, ...] pre-computed sample points with rounded coords",
    )
    geometry_fetched_at = models.DateTimeField(null=True, blank=True)
    vertex_times = models.JSONField(null=True, blank=True, help_text="Floating-point elapsed seconds at every polyline vertex")

    # Pre-rendered route-list glyph: simplified path + the weather fields the frontend
    # scorer reads, for the next departure. Written by refresh_route_thumbnail so the
    # list endpoint never has to parse a forecast cell. See core/thumbnails.py.
    thumbnail = models.JSONField(null=True, blank=True)
    thumbnail_computed_at = models.DateTimeField(null=True, blank=True)

    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # A list, not a tuple: migrations compare it as written, and 0001 stores a list.
        ordering: ClassVar[list[str]] = ["-created_at"]

    @property
    def start_lat(self) -> float:
        return self.start_point.y

    @property
    def start_lon(self) -> float:
        return self.start_point.x

    @property
    def dest_lat(self) -> float:
        return self.destination_point.y

    @property
    def dest_lon(self) -> float:
        return self.destination_point.x

    @property
    def polyline_coordinates(self) -> list[list[float]] | None:
        if self.polyline is None:
            return None
        return [[float(lon), float(lat)] for lon, lat in self.polyline.coords]

    def __str__(self):
        return self.name


class ForecastCell(models.Model):
    """Cached deterministic weather forecast for a ~1 km² grid cell.

    Stores the full time-series API response (Open-Meteo minutely_15 + hourly,
    or OWM hourly fallback) so all time steps are available for lookup.
    """

    lat_r = models.FloatField(help_text="Latitude rounded to 2 decimal places (~1 km)")
    lon_r = models.FloatField(help_text="Longitude rounded to 2 decimal places (~1 km)")
    day_key = models.DateField(help_text="The 'today' date when the forecast was fetched")
    forecast_days = models.IntegerField(help_text="Number of forecast days requested (1–16)")

    data = models.JSONField(help_text="Raw API response dict (minutely_15 + hourly blocks)")

    source = models.CharField(max_length=50, help_text='"open-meteo" or "openweathermap"')
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("lat_r", "lon_r", "day_key", "source"),)
        indexes = (
            models.Index(fields=["lat_r", "lon_r", "day_key"]),
            models.Index(fields=["fetched_at"]),
        )

    def __str__(self):
        return f"ForecastCell({self.lat_r}, {self.lon_r}, {self.day_key}, {self.source})"


class EnsembleCell(models.Model):
    """Cached ensemble precipitation probability for a ~1 km² grid cell.

    Stores the full ensemble API response (hourly, multi-model) so POP
    can be computed for any ETA within the forecast window.
    """

    lat_r = models.FloatField(help_text="Latitude rounded to 2 decimal places")
    lon_r = models.FloatField(help_text="Longitude rounded to 2 decimal places")
    day_key = models.DateField(help_text="The 'today' date when the forecast was fetched")
    forecast_days = models.IntegerField(help_text="Number of forecast days requested (1–16)")

    data = models.JSONField(help_text="Raw ensemble API response (hourly precipitation series)")

    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("lat_r", "lon_r", "day_key"),)
        indexes = (
            models.Index(fields=["lat_r", "lon_r", "day_key"]),
            models.Index(fields=["fetched_at"]),
        )

    def __str__(self):
        return f"EnsembleCell({self.lat_r}, {self.lon_r}, {self.day_key})"


class StationLookup(models.Model):
    """The Weather Underground stations nearest one lookup cell (~4-5 km).

    Stations rarely come and go, so this is kept for weeks and spares the call budget.
    """

    lat_c = models.FloatField(help_text="Latitude rounded to 1/20 degree")
    lon_c = models.FloatField(help_text="Longitude rounded to 1/20 degree")
    stations = models.JSONField(help_text="[{id, lat, lon, qc, updated}, ...], nearest first")
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (("lat_c", "lon_c"),)

    def __str__(self):
        return f"StationLookup({self.lat_c}, {self.lon_c})"


class StationObservation(models.Model):
    """The latest reading of one Weather Underground personal weather station.

    Kept apart from ``ForecastCell``: a reading belongs to a station, not a grid cell, and
    it is only worth anything for minutes, not the two hours a forecast cell lives.
    """

    station_id = models.CharField(max_length=64, unique=True)
    lat = models.FloatField()
    lon = models.FloatField()
    observed_at = models.DateTimeField()
    temp = models.FloatField(null=True, blank=True, help_text="°C")
    precip_rate = models.FloatField(null=True, blank=True, help_text="mm/h")
    qc_status = models.IntegerField(null=True, blank=True, help_text="-1 unchecked, 0 possibly wrong, 1 passed")
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = (models.Index(fields=["observed_at"]),)

    def __str__(self):
        return f"StationObservation({self.station_id}, {self.observed_at})"


class ForecastJob(models.Model):
    """One forecast computation, carried out by background tasks.

    The forecast endpoints do no work themselves: they create (or find) a job, enqueue
    ``plan_forecast_job`` and return. The job then accumulates progress as individual
    cell tasks settle, ``compute_route_weather_job`` stores intermediate weather,
    and ``assemble_forecast_job`` writes the finished payload into
    ``result``. The browser watches a job over the WebSocket in ``core/consumers.py``.
    """

    class Kind(models.TextChoices):
        ADHOC = "adhoc", "Ad-hoc route"
        ROUTE = "route", "Saved route"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PLANNING = "planning", "Planning"
        FETCHING = "fetching", "Fetching cells"
        ASSEMBLING = "assembling", "Assembling"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    TERMINAL_STATUSES = (Status.DONE, Status.FAILED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Identical requests collapse onto one job. The owner is part of the key because
    # `result` is already entitlement-stripped when it is stored -- a free account and a
    # Pro account asking for the same ride must not share a row.
    key = models.CharField(max_length=64, unique=True)

    kind = models.CharField(max_length=16, choices=Kind.choices)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="forecast_jobs",
    )
    params = models.JSONField(help_text="The request that asked for this forecast")
    # Resolved once by plan_forecast_job and reused by compute_route_weather_job, so the
    # GraphHopper call happens at most once per job even though the two tasks usually run
    # in different worker processes (the alru_cache in weather.py is per process).
    geometry = models.JSONField(
        null=True,
        blank=True,
        help_text="polyline, sample_points, total_seconds, total_distance_m",
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    cells_total = models.IntegerField(default=0)
    # Counts failed cells too. A cell whose providers are all down must still settle, or
    # one dead coordinate would leave the job fetching forever.
    cells_settled = models.IntegerField(default=0)
    # How many of the settled cells stored nothing (a provider error, a 429). A finished job
    # with failures is reused only briefly, so a short provider outage does not hide the
    # missing data for the whole MAX_CELL_AGE.
    cells_failed = models.IntegerField(default=0)
    # Bounds the re-defer loop in plan_forecast_job when route geometry never arrives.
    attempts = models.IntegerField(default=0)

    # Internal handoff: JSON RouteWeatherOut plus the entitlements used to compute it.
    computed_weather = models.JSONField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = (
            models.Index(fields=["status"]),
            models.Index(fields=["updated_at"]),
        )

    def __str__(self):
        return f"ForecastJob({self.kind}, {self.status}, {self.cells_settled}/{self.cells_total})"

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES
