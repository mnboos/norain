from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils import timezone

from .models import (
    CellFetchLease,
    ElevationProfile,
    EnsembleCell,
    ForecastCell,
    ForecastJob,
    Journey,
    JourneyDay,
    JourneyStage,
    Poi,
    ProcessedStripeEvent,
    PushSubscription,
    RecurringRoute,
    RideBriefing,
    RouteComment,
    RouteLike,
    RoutePhoto,
    StationLookup,
    StationObservation,
    Subscription,
    User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Django's user admin plus the Meteolane fields.

    Email verification is on allauth's own "Email addresses" admin page.
    """

    list_display = ("username", "email", "signup_completed", "is_active", "is_staff", "created_at")
    list_filter = ("signup_completed", "is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email")
    readonly_fields = ("created_at",)
    actions = ["grant_beta_plus"]

    @admin.action(description="Grant 90 days of complimentary Plus (no payment)")
    def grant_beta_plus(self, request, queryset):
        for user in queryset:
            subscription, _ = Subscription.objects.get_or_create(user=user)
            subscription.complimentary_until = max(
                subscription.complimentary_until or timezone.now(), timezone.now() + timedelta(days=90)
            )
            subscription.save(update_fields=["complimentary_until", "updated_at"])
        self.message_user(request, "Complimentary Plus granted; no payments or messages were sent.")

    fieldsets = (*DjangoUserAdmin.fieldsets, ("Meteolane", {"fields": ["signup_completed", "created_at"]}))


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Set a tier by hand here — entitlements read this row, not Stripe."""

    list_display = (
        "user",
        "plan",
        "status",
        "current_period_end",
        "cancel_at_period_end",
        "complimentary_until",
        "updated_at",
    )
    list_filter = ("plan", "status")
    search_fields = ("user__email", "user__username", "stripe_customer_id", "stripe_subscription_id")
    list_select_related = ("user",)


@admin.register(ProcessedStripeEvent)
class ProcessedStripeEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "event_type", "received_at")
    list_filter = ("event_type",)
    search_fields = ("event_id",)


@admin.register(RecurringRoute)
class RecurringRouteAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "profile", "schedule_description", "active", "visibility", "created_at")
    list_filter = ("active", "profile", "visibility")
    search_fields = ("name", "owner__email", "owner__username", "start_name", "dest_name")
    list_select_related = ("owner",)


@admin.register(ForecastCell)
class ForecastCellAdmin(admin.ModelAdmin):
    list_display = ("lat_r", "lon_r", "day_key", "source", "forecast_days", "fetched_at")
    list_filter = ("source", "day_key")
    search_fields = ("lat_r", "lon_r")


@admin.register(EnsembleCell)
class EnsembleCellAdmin(admin.ModelAdmin):
    list_display = ("lat_r", "lon_r", "day_key", "forecast_days", "fetched_at")
    list_filter = ("day_key",)
    search_fields = ("lat_r", "lon_r")


@admin.register(CellFetchLease)
class CellFetchLeaseAdmin(admin.ModelAdmin):
    list_display = ("kind", "lat_r", "lon_r", "day_key", "expires_at")
    list_filter = ("kind",)


@admin.register(StationLookup)
class StationLookupAdmin(admin.ModelAdmin):
    list_display = ("lat_c", "lon_c", "fetched_at")
    search_fields = ("lat_c", "lon_c")


@admin.register(StationObservation)
class StationObservationAdmin(admin.ModelAdmin):
    list_display = ("station_id", "observed_at", "temp", "precip_rate", "qc_status", "fetched_at")
    list_filter = ("qc_status",)
    search_fields = ("station_id",)


@admin.register(ForecastJob)
class ForecastJobAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "status", "owner", "cells_settled", "cells_total", "cells_failed", "updated_at")
    list_filter = ("kind", "status")
    search_fields = ("key", "owner__email", "owner__username")
    list_select_related = ("owner",)
    raw_id_fields = ("owner",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "endpoint", "created_at")
    search_fields = ("user__email", "user__username")
    list_select_related = ("user",)
    raw_id_fields = ("user",)


@admin.register(RideBriefing)
class RideBriefingAdmin(admin.ModelAdmin):
    list_display = ("route", "departure", "channel", "status", "due_at", "sent_at")
    list_filter = ("status", "channel")
    search_fields = ("route__name",)
    list_select_related = ("route",)
    raw_id_fields = ("route", "job")


@admin.register(Poi)
class PoiAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "osm_ref")
    list_filter = ("category",)
    search_fields = ("name", "osm_ref")


class JourneyDayInline(admin.TabularInline):
    model = JourneyDay
    fields = ("index", "date", "lodging_missing", "weather_routed")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Journey)
class JourneyAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "start_date", "plan_status", "plan_revision", "updated_at")
    list_filter = ("plan_status", "profile")
    search_fields = ("name", "owner__email", "owner__username", "start_name", "dest_name")
    list_select_related = ("owner",)
    raw_id_fields = ("owner",)
    inlines = (JourneyDayInline,)


class JourneyStageInline(admin.TabularInline):
    model = JourneyStage
    fields = ("rank", "total_distance_m", "total_seconds", "detour_m")
    readonly_fields = fields
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(JourneyDay)
class JourneyDayAdmin(admin.ModelAdmin):
    list_display = ("journey", "index", "date", "lodging_missing", "weather_routed")
    list_filter = ("lodging_missing", "weather_routed")
    list_select_related = ("journey",)
    raw_id_fields = ("journey",)
    inlines = (JourneyStageInline,)


@admin.register(JourneyStage)
class JourneyStageAdmin(admin.ModelAdmin):
    list_display = ("day", "rank", "total_distance_m", "total_seconds", "detour_m")
    list_select_related = ("day__journey",)
    raw_id_fields = ("day",)


@admin.register(ElevationProfile)
class ElevationProfileAdmin(admin.ModelAdmin):
    list_display = ("key", "created_at")
    search_fields = ("key",)


# Moderation: public routes carry what users wrote and uploaded. Deleting here removes it
# from the public page at once (the photo files go with the row, see core.signals).
@admin.register(RouteComment)
class RouteCommentAdmin(admin.ModelAdmin):
    list_display = ("author", "route", "created_at", "body")
    search_fields = ("body", "author__username", "route__name")
    raw_id_fields = ("route", "author")


@admin.register(RoutePhoto)
class RoutePhotoAdmin(admin.ModelAdmin):
    list_display = ("uploader", "route", "caption", "created_at")
    search_fields = ("caption", "uploader__username", "route__name")
    raw_id_fields = ("route", "uploader")
    exclude = ("location",)


@admin.register(RouteLike)
class RouteLikeAdmin(admin.ModelAdmin):
    list_display = ("user", "route", "created_at")
    search_fields = ("user__username", "route__name")
    raw_id_fields = ("route", "user")
