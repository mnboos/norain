<route lang="json5">
{
    name: "route-detail",
    meta: { title: "Route", requiresAuth: true },
}
</route>

<script setup lang="ts">
import { computed, watch } from "vue";
import { useRoute } from "vue-router";
import { symSharpArrowBack } from "@quasar/extras/material-symbols-sharp";
import RouteDetailPanel from "@/components/RouteDetailPanel.vue";
import { useSession } from "@/composables/useSession";
import { nextDepartureParts, useRecurringRoute } from "@/queries/recurringRoutes";
import { recordRouteOpened } from "@/utils/recentRoutes";

const currentRoute = useRoute();
const routeId = computed(() => String(currentRoute.params.id));
const { session } = useSession();

const { data: route, isLoading, error } = useRecurringRoute(routeId);

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
    <q-page class="column">
        <!-- Once the route is loaded, the back button moves into the panel's one-line header. -->
        <q-btn v-if="!route" flat :icon="symSharpArrowBack" label="Zurück" to="/" class="q-ma-sm" />

        <div v-if="isLoading" class="text-center q-mt-xl">
            <q-spinner-dots size="3rem" />
        </div>

        <q-banner v-else-if="error || !route" class="bg-tint-error q-ma-md" rounded>Route nicht gefunden.</q-banner>

        <q-tabs v-if="route?.returnRouteId || route?.parentRouteId" dense>
            <q-route-tab :to="`/routes/${route.parentRouteId ?? route.id}`" label="Hinfahrt" exact />
            <q-route-tab :to="`/routes/${route.returnRouteId ?? route.id}`" label="Rückfahrt" exact />
        </q-tabs>
        <RouteDetailPanel
            v-if="route"
            :key="routeId"
            :route="route"
            :departure-date="departureDate"
            :departure-time="departureTime"
            class="col"
        >
            <template #back>
                <q-btn flat round dense :icon="symSharpArrowBack" to="/" aria-label="Zurück" />
            </template>
        </RouteDetailPanel>
    </q-page>
</template>
