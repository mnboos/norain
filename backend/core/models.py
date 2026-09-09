import uuid

from django.db import models


class RecurringRoute(models.Model):
    """A user-configured bike route with a cron schedule for recurring weather checks."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")

    start_lat = models.FloatField()
    start_lon = models.FloatField()
    start_name = models.CharField(max_length=300)

    dest_lat = models.FloatField()
    dest_lon = models.FloatField()
    dest_name = models.CharField(max_length=300)

    profile = models.CharField(
        max_length=50,
        default="bike",
        help_text="GraphHopper routing profile: bike, ebike, fast_ebike, car, foot",
    )

    schedule_cron = models.CharField(max_length=100, help_text="5-field cron expression")
    schedule_description = models.CharField(max_length=200, help_text="Human-readable, e.g. 'Every Monday at 08:00'")

    # Pre-computed route geometry (populated by background task on create/update)
    polyline = models.JSONField(null=True, blank=True, help_text="[[lon, lat], ...] full route polyline")
    total_seconds = models.IntegerField(null=True, blank=True)
    total_distance_m = models.FloatField(null=True, blank=True)
    sample_points = models.JSONField(
        null=True,
        blank=True,
        help_text="[{lat, lon, lat_r, lon_r, elapsed_s, idx}, ...] pre-computed sample points with rounded coords",
    )
    geometry_fetched_at = models.DateTimeField(null=True, blank=True)

    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

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
        unique_together = [("lat_r", "lon_r", "day_key", "source")]
        indexes = [
            models.Index(fields=["lat_r", "lon_r", "day_key"]),
            models.Index(fields=["fetched_at"]),
        ]

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
        unique_together = [("lat_r", "lon_r", "day_key")]
        indexes = [
            models.Index(fields=["lat_r", "lon_r", "day_key"]),
            models.Index(fields=["fetched_at"]),
        ]

    def __str__(self):
        return f"EnsembleCell({self.lat_r}, {self.lon_r}, {self.day_key})"
