<route lang="json5">
{
  name: "route-detail",
  meta: { title: "Route", requiresAuth: true }
}
</route>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";
import { symSharpArrowBack } from "@quasar/extras/material-symbols-sharp";
import RouteDetailPanel from "@/components/RouteDetailPanel.vue";
import { useRecurringRoute } from "@/queries/recurringRoutes";

const currentRoute = useRoute();
const routeId = computed(() => String(currentRoute.params.id));

const {
    data: route,
    isLoading,
    error,
} = useRecurringRoute(routeId);

const departureDate = computed(() => {
    const next = route.value?.nextDeparture;
    return next ? new Date(next).toISOString().slice(0, 10) : "";
});

const departureTime = computed(() => {
    const next = route.value?.nextDeparture;
    return next ? new Date(next).toTimeString().slice(0, 5) : "";
});
</script>

<template>
    <q-page>
        <!-- Once the route is loaded, the back button moves into the panel's one-line header. -->
        <q-btn v-if="!route" flat :icon="symSharpArrowBack" label="Zurück" to="/" class="q-ma-sm" />

        <div v-if="isLoading" class="text-center q-mt-xl">
            <q-spinner-dots size="3rem" />
        </div>

        <q-banner v-else-if="error || !route" class="bg-tint-error q-ma-md" rounded>
            Route nicht gefunden.
        </q-banner>

        <RouteDetailPanel
            v-else
            :route="route"
            :departure-date="departureDate"
            :departure-time="departureTime"
        >
            <template #back>
                <q-btn flat round dense :icon="symSharpArrowBack" to="/" aria-label="Zurück" />
            </template>
        </RouteDetailPanel>
    </q-page>
</template>
