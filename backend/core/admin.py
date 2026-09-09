from django.contrib import admin

from .models import EnsembleCell, ForecastCell, RecurringRoute


@admin.register(RecurringRoute)
class RecurringRouteAdmin(admin.ModelAdmin):
    list_display = ["name", "profile", "schedule_description", "active", "created_at"]
    list_filter = ["active", "profile"]
    search_fields = ["name", "start_name", "dest_name"]


@admin.register(ForecastCell)
class ForecastCellAdmin(admin.ModelAdmin):
    list_display = ["lat_r", "lon_r", "day_key", "source", "forecast_days", "fetched_at"]
    list_filter = ["source", "day_key"]
    search_fields = ["lat_r", "lon_r"]


@admin.register(EnsembleCell)
class EnsembleCellAdmin(admin.ModelAdmin):
    list_display = ["lat_r", "lon_r", "day_key", "forecast_days", "fetched_at"]
    list_filter = ["day_key"]
    search_fields = ["lat_r", "lon_r"]
