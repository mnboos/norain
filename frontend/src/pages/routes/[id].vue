<route lang="json5">
{
    name: "route-detail",
    meta: { titleKey: "pages.route", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { tp } from "@/i18n";
import { symSharpArrowBack } from "@quasar/extras/material-symbols-sharp";
import RouteDetailPanel from "@/components/RouteDetailPanel.vue";
import { useSession } from "@/composables/useSession";
import { nextDepartureParts, useRecurringRoute } from "@/queries/recurringRoutes";
import { recordRouteOpened } from "@/utils/recentRoutes";
import { nextRideId } from "@/utils/nextRide";

const { t } = useI18n();
const currentRoute = useRoute();
const router = useRouter();
const routeId = computed(() => String(currentRoute.params.id));
const { session } = useSession();

const { data: route, isLoading, error } = useRecurringRoute(routeId);

// A route opened from the dashboard or a bookmark means the next ride in either
// direction. An explicit tab selection must remain selectable even when it is later.
const nextDirectionId = computed(() =>
    route.value && currentRoute.query.direction !== "outbound" ? nextRideId(route.value) : routeId.value,
);
watch(
    nextDirectionId,
    id => {
        if (id !== routeId.value) void router.replace({ path: `/routes/${id}`, query: currentRoute.query });
    },
    { immediate: true },
);

// Remembered so the dashboard can load this route's forecast ahead next time.
watch(
    routeId,
    id => {
        const user = session.value.user;
        recordRouteOpened(user?.id ?? user?.username ?? "", id);
    },
    { immediate: true },
);

// The same departure the dashboard prefetches, so the forecast is found in the cache.
const departure = computed(() => (route.value ? nextDepartureParts(route.value) : { date: "", time: "" }));
const departureDate = computed(() => departure.value.date);
const departureTime = computed(() => departure.value.time);
</script>

<template>
    <q-page class="q-pa-md column">
        <!-- Once the route is loaded, the back button moves into the panel's one-line header. -->
        <q-btn v-if="!route" flat :icon="symSharpArrowBack" :label="t('common.back')" to="/routes" class="self-start" />

        <div v-if="isLoading" class="text-center q-mt-xl">
            <q-spinner-dots size="3rem" />
        </div>

        <q-banner v-else-if="error || !route" class="bg-tint-error q-mt-md" rounded>{{ t("routes.notFound") }}</q-banner>

        <q-tabs v-if="route?.returnRouteId || route?.parentRouteId" dense align="left" class="q-mb-md">
            <q-route-tab
                :to="{ path: `/routes/${route.parentRouteId ?? route.id}`, query: { direction: 'outbound' } }"
                :label="tp(route.profile, 'routes.outbound')"
                exact
            />
            <q-route-tab :to="`/routes/${route.returnRouteId ?? route.id}`" :label="tp(route.profile, 'routes.return')" exact />
        </q-tabs>
        <RouteDetailPanel
            v-if="route && nextDirectionId === routeId"
            :key="routeId"
            :route="route"
            :departure-date="departureDate"
            :departure-time="departureTime"
            class="col"
        >
            <template #back>
                <q-btn flat round dense :icon="symSharpArrowBack" to="/routes" :aria-label="t('common.back')" />
            </template>
        </RouteDetailPanel>
    </q-page>
</template>
