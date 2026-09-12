from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import EnsembleCell, ForecastCell, ProcessedStripeEvent, RecurringRoute, Subscription, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Django's user admin plus the NoRain verification fields."""

    list_display = ["username", "email", "email_verified", "is_active", "is_staff", "created_at"]
    list_filter = ["email_verified", "is_active", "is_staff", "is_superuser"]
    search_fields = ["username", "email"]
    readonly_fields = ["created_at"]
    # email_verified gates sign-in (see IdentityBackend), so it belongs somewhere visible.
    fieldsets = [*DjangoUserAdmin.fieldsets, ("NoRain", {"fields": ["email_verified", "created_at"]})]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Set a tier by hand here — entitlements read this row, not Stripe."""

    list_display = ["user", "plan", "status", "current_period_end", "cancel_at_period_end", "updated_at"]
    list_filter = ["plan", "status"]
    search_fields = ["user__email", "user__username", "stripe_customer_id", "stripe_subscription_id"]
    list_select_related = ["user"]


@admin.register(ProcessedStripeEvent)
class ProcessedStripeEventAdmin(admin.ModelAdmin):
    list_display = ["event_id", "event_type", "received_at"]
    list_filter = ["event_type"]
    search_fields = ["event_id"]


@admin.register(RecurringRoute)
class RecurringRouteAdmin(admin.ModelAdmin):
    list_display = ["name", "owner", "profile", "schedule_description", "active", "created_at"]
    list_filter = ["active", "profile"]
    search_fields = ["name", "owner__email", "owner__username", "start_name", "dest_name"]
    list_select_related = ["owner"]


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
