<script setup lang="ts">
import { computed, toRefs, ref, watch } from "vue";
import { symSharpCloudOff, symSharpMap } from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import ForecastDetails from "@/components/ForecastDetails.vue";
import WeatherSummaryCard from "@/components/WeatherSummaryCard.vue";
import WeatherSections from "@/components/WeatherSections.vue";
import WeatherCharts from "@/components/WeatherCharts.vue";
import NiceMap from "@/components/NiceMap.vue";
import { useRecurringRoute, useRecurringRouteForecast } from "@/queries/recurringRoutes";

const props = defineProps<{
    route: RecurringRouteOut;
    departureDate: string;
    departureTime: string;
}>();

const { route, departureDate, departureTime } = toRefs(props);

const routeId = computed(() => route.value.id);

const refetchInterval = computed(() => (!route.value.hasGeometry ? 3000 : false));

// Fetch the full route data (for geometry status polling)
const { data: routeDetail } = useRecurringRoute(routeId, refetchInterval);

const hasGeometry = computed(() => routeDetail.value?.hasGeometry ?? route.value.hasGeometry);
const departureEnabled = computed(() => !!(hasGeometry.value && !!departureDate.value && !!departureTime.value));
const {
    data: forecast,
    isFetching: forecastLoading,
    error: forecastError,
    progress: forecastProgress,
} = useRecurringRouteForecast(routeId, departureDate, departureTime, departureEnabled);

// The forecast is assembled from one grid cell per ~1 km² of route, fetched by background
// workers. Showing how many have landed turns an indefinite wait into a determinate one.
const forecastProgressPercent = computed(() => {
    const progress = forecastProgress.value;
    if (!progress?.cellsTotal) return undefined;
    return Math.round((100 * progress.cellsSettled) / progress.cellsTotal);
});

const selectedSample = ref(0);
watch(forecast, () => {
    selectedSample.value = 0;
});

function profileLabel(profile: string): string {
    const labels: Record<string, string> = {
        bike: "Velo",
        ebike: "E-Bike",
        fast_ebike: "S-Pedelec",
    };
    return labels[profile] ?? profile;
}
</script>

<template>
    <div class="q-px-md q-pb-md q-pt-xs">
        <!-- Compact header: back link, name and metadata on one line, so the whole forecast
             (summary, charts, map) fits the first screen without scrolling. -->
        <div class="row items-center no-wrap q-gutter-x-sm q-mb-xs">
            <slot name="back" />
            <div class="col">
                <span class="text-subtitle1 text-weight-medium q-mr-sm">{{ route.name }}</span>
                <span class="text-caption text-muted">
                    {{ route.startName }} → {{ route.destName }} · {{ profileLabel(route.profile) }} ·
                    {{ route.scheduleDescription }}
                </span>
                <div v-if="route.description" class="text-caption text-muted">{{ route.description }}</div>
            </div>
            <q-btn
                v-if="forecast"
                flat
                dense
                no-caps
                color="primary"
                :icon="symSharpMap"
                label="Auf Karte anzeigen"
                :to="`/map?route=${route.id}`"
            />
        </div>

        <!-- A. Geometry pending -->
        <q-banner v-if="!hasGeometry" class="bg-tint-warn q-mb-sm" rounded>
            <template #avatar>
                <q-spinner-dots size="1.5rem" color="accent" />
            </template>
            Route wird berechnet… Die Streckendaten werden im Hintergrund geladen.
        </q-banner>

        <!-- B. No forecast available -->
        <q-banner v-else-if="!route.forecastAvailable && !forecastLoading" class="bg-tint-neutral q-mb-sm" rounded>
            <template #avatar>
                <q-icon :name="symSharpCloudOff" class="text-muted" />
            </template>
            Noch keine Vorhersage möglich. Die Wettervorhersage ist erst näher am Abfahrtstermin verfügbar.
        </q-banner>

        <!-- C. Forecast loaded -->
        <template v-if="forecast">
            <WeatherSummaryCard :forecast="forecast" class="q-mb-sm" />
            <WeatherSections v-if="forecast.sections?.length" :sections="forecast.sections" />
            <ForecastDetails v-model:selected-sample="selectedSample" :forecast="forecast" />
            <!-- Square tiles: one row of four on wide screens, 2x2 on tablets, stacked on phones. -->
            <div class="row q-col-gutter-md q-mt-none">
                <WeatherCharts
                    :job-id="forecast.jobId"
                    :version="forecast.version"
                    @select-sample="selectedSample = $event"
                />
                <div class="col-12 col-sm-6 col-md-3">
                    <q-card flat bordered class="square-tile">
                        <NiceMap
                            :route-weather="forecast"
                            :selected-sample="selectedSample"
                            height="100%"
                            @select-sample="selectedSample = $event"
                        />
                    </q-card>
                </div>
            </div>
        </template>

        <!-- Loading -->
        <div v-else-if="forecastLoading && hasGeometry" class="text-center q-mt-xl">
            <q-circular-progress
                v-if="forecastProgressPercent !== undefined"
                show-value
                :value="forecastProgressPercent"
                size="3rem"
                :thickness="0.2"
                color="primary"
                track-color="grey-3"
            />
            <q-spinner-dots v-else size="3rem" />
            <p class="text-grey">Wetterdaten werden geladen…</p>
        </div>

        <!-- Error -->
        <q-banner v-else-if="forecastError" class="bg-tint-error q-mt-md" rounded>
            Fehler beim Laden der Wetterdaten. Bitte versuche es später erneut.
        </q-banner>
    </div>
</template>
