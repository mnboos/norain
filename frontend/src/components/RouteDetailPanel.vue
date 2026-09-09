<script setup lang="ts">
import { computed, toRefs } from "vue";
import { useQuery } from "@tanstack/vue-query";
import { symSharpCloudOff, symSharpMap } from "@quasar/extras/material-symbols-sharp";
import { DefaultApi } from "@norain/api";
import type { RecurringRouteOut } from "@norain/api";
import WeatherSummaryCard from "@/components/WeatherSummaryCard.vue";
import WeatherSections from "@/components/WeatherSections.vue";
import WeatherCharts from "@/components/WeatherCharts.vue";
import NiceMap from "@/components/NiceMap.vue";

const api = new DefaultApi();

const props = defineProps<{
    route: RecurringRouteOut;
    departureDate: string;
    departureTime: string;
}>();

const { route, departureDate, departureTime } = toRefs(props);

const routeId = computed(() => route.value.id);

const refetchInterval = computed(() => (!route.value.hasGeometry ? 3000 : false));

// Fetch the full route data (for geometry status polling)
const { data: routeDetail } = useQuery({
    queryKey: [api, "route", routeId],
    queryFn: () => api.coreRoutesApiGetRoute({ routeId: routeId.value }),
    refetchInterval,
});

const hasGeometry = computed(() => routeDetail.value?.hasGeometry ?? route.value.hasGeometry);
const departureEnabled = computed(() => !!(hasGeometry.value && !!departureDate.value && !!departureTime.value));
const {
    data: forecast,
    isFetching: forecastLoading,
    error: forecastError,
} = useQuery({
    queryKey: [api, "routeForecast", routeId, departureDate, departureTime],
    queryFn: () =>
        api.coreRoutesApiRouteForecast({
            routeId: routeId.value,
            date: departureDate.value,
            time: departureTime.value,
        }),
    enabled: departureEnabled,
    staleTime: 5 * 60 * 1000,
});

function profileLabel(profile: string): string {
    const labels: Record<string, string> = {
        bike: "Velo",
        ebike: "E-Bike",
        fast_ebike: "S-Pedelec",
        car: "Auto",
        foot: "Fuss",
    };
    return labels[profile] ?? profile;
}
</script>

<template>
    <div class="q-pa-md">
        <!-- A. Geometry pending -->
        <q-banner v-if="!hasGeometry" class="bg-orange-1 q-mb-md" rounded>
            <template #avatar>
                <q-spinner-dots size="1.5rem" color="orange" />
            </template>
            Route wird berechnet… Die Streckendaten werden im Hintergrund geladen.
        </q-banner>

        <!-- B. No forecast available -->
        <q-banner v-else-if="!route.forecastAvailable && !forecastLoading" class="bg-grey-3 q-mb-md" rounded>
            <template #avatar>
                <q-icon :name="symSharpCloudOff" color="grey-7" />
            </template>
            Noch keine Vorhersage möglich. Die Wettervorhersage ist erst näher am Abfahrtstermin verfügbar.
        </q-banner>

        <!-- Route metadata -->
        <q-card class="q-mb-md">
            <q-card-section>
                <div class="text-h6">{{ route.name }}</div>
                <div v-if="route.description" class="text-body2 text-grey-7">{{ route.description }}</div>
                <div class="row q-gutter-md q-mt-sm text-body2">
                    <div>
                        <span class="text-weight-medium">{{ route.startName }}</span>
                        →
                        <span class="text-weight-medium">{{ route.destName }}</span>
                    </div>
                    <div>{{ profileLabel(route.profile) }}</div>
                    <div>{{ route.scheduleDescription }}</div>
                </div>
            </q-card-section>
        </q-card>

        <!-- C. Forecast loaded -->
        <template v-if="forecast">
            <WeatherSummaryCard :forecast="forecast" />
            <WeatherSections v-if="forecast.sections" :sections="forecast.sections" />
            <!-- Square tiles in a 2-column grid so charts + map fit on one screen. -->
            <div class="row q-col-gutter-md q-mt-none">
                <WeatherCharts v-if="forecast.figures" :figures="forecast.figures" />
                <div class="col-12 col-sm-6">
                    <q-card flat bordered class="square-tile">
                        <NiceMap :route-weather="forecast" height="100%" />
                    </q-card>
                </div>
            </div>
            <div class="q-mt-md">
                <q-btn
                    outline
                    color="primary"
                    :icon="symSharpMap"
                    label="Auf Karte anzeigen"
                    :to="`/map?route=${route.id}`"
                />
            </div>
        </template>

        <!-- Loading -->
        <div v-else-if="forecastLoading && hasGeometry" class="text-center q-mt-xl">
            <q-spinner-dots size="3rem" />
            <p class="text-grey">Wetterdaten werden geladen…</p>
        </div>

        <!-- Error -->
        <q-banner v-else-if="forecastError" class="bg-red-1 q-mt-md" rounded>
            Fehler beim Laden der Wetterdaten. Bitte versuche es später erneut.
        </q-banner>
    </div>
</template>
