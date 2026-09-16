<script setup lang="ts">
import { computed, ref, toRefs, watch } from "vue";
import { useQuasar } from "quasar";
import { symSharpCloudOff } from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import WeatherSummaryCard from "@/components/WeatherSummaryCard.vue";
import WeatherCharts from "@/components/WeatherCharts.vue";
import NiceMap from "@/components/NiceMap.vue";
import { useRecurringRoute, useRecurringRouteForecast } from "@/queries/recurringRoutes";
import { pathExtent } from "@/utils/routeThumbnail";

const PROFILE_LABELS: Record<string, string> = {
    bike: "Velo",
    ebike: "E-Bike",
    fast_ebike: "S-Pedelec",
};

const props = defineProps<{
    route: RecurringRouteOut;
    departureDate: string;
    departureTime: string;
}>();

const { route, departureDate, departureTime } = toRefs(props);

const routeId = computed(() => route.value.id);
const hasGeometry = computed(() => !!route.value.hasGeometry);
const profileLabel = computed(() => PROFILE_LABELS[route.value.profile] ?? route.value.profile);

// Polls until the geometry is built. The page reads the same query key, so `route` updates with it.
useRecurringRoute(routeId, () => (hasGeometry.value ? false : 3000));

const {
    data: forecast,
    isFetching: forecastLoading,
    error: forecastError,
    progress: forecastProgress,
} = useRecurringRouteForecast(
    routeId,
    departureDate,
    departureTime,
    () => hasGeometry.value && !!departureDate.value && !!departureTime.value,
);

// The forecast is assembled from one grid cell per ~1 km² of route, fetched by background
// workers. Showing how many have landed turns an indefinite wait into a determinate one.
const forecastProgressPercent = computed(() => {
    const progress = forecastProgress.value;
    if (!progress?.cellsTotal) return undefined;
    return Math.round((100 * progress.cellsSettled) / progress.cellsTotal);
});

// The map takes the route's shape. A north-south route gets a tall map with the charts stacked
// beside it; everything else (and every phone, which is tall anyway) the charts in a row above a
// wide map.
const $q = useQuasar();
const chartsBeside = computed(() => {
    if (!$q.screen.gt.sm) return false;
    const { width, height } = pathExtent(forecast.value?.line ?? []);
    return height > width;
});

const selectedSample = ref(0);
watch(forecast, () => {
    selectedSample.value = 0;
});
</script>

<template>
    <q-card flat class="col column transparent">
        <q-card-section class="no-padding">
            <q-item>
                <q-item-section side>
                    <slot name="back" />
                </q-item-section>
                <q-item-section>
                    <q-item-label>
                        <h1 class="text-subtitle1 text-weight-medium q-ma-none">{{ route.name }}</h1>
                    </q-item-label>
                    <q-item-label caption>
                        {{ route.startName }} → {{ route.destName }} · {{ route.scheduleDescription }}
                    </q-item-label>
                    <q-item-label v-if="route.description" caption>{{ route.description }}</q-item-label>
                </q-item-section>
                <q-item-section side>
                    <q-badge outline color="primary" :label="profileLabel" />
                </q-item-section>
            </q-item>
        </q-card-section>
        <q-separator />

        <q-card-section class="no-padding">
            <q-banner v-if="!hasGeometry" class="bg-tint-warn">
                <template #avatar>
                    <q-spinner-dots size="1.5rem" color="accent" />
                </template>
                Route wird berechnet...
            </q-banner>
            <q-banner v-else-if="forecastError" class="bg-tint-error">
                Fehler beim Laden der Wetterdaten. Bitte versuche es später erneut.
            </q-banner>
            <q-banner v-else-if="!route.forecastAvailable && !forecastLoading" class="bg-tint-neutral">
                <template #avatar>
                    <q-icon :name="symSharpCloudOff" class="text-muted" />
                </template>
                Noch keine Vorhersage möglich. Die Wettervorhersage ist erst näher am Abfahrtstermin verfügbar.
            </q-banner>
        </q-card-section>
        <q-card-section v-if="forecast" class="no-padding">
            <WeatherSummaryCard :forecast="forecast" class="transparent" />
        </q-card-section>
        <!-- Grid reserves the square charts' intrinsic height above the map; in a flex
             column their wrapper can collapse to zero and let the map cover them.
             col-grow lets short screens scroll instead of squeezing the forecast.
             Beside a tall map, `reverse` puts the charts on the right while the markup keeps one order. -->
        <q-card-section
            v-if="forecast"
            class="no-padding col-grow"
            :class="chartsBeside ? 'row reverse no-wrap' : 'forecast-stacked'"
        >
            <q-card flat class="col-auto transparent" :class="{ 'q-ml-xs': chartsBeside }">
                <WeatherCharts
                    :job-id="forecast.jobId"
                    :version="forecast.version"
                    :selected-sample="selectedSample"
                    :samples="forecast.samples"
                    :vertical="chartsBeside"
                    @select-sample="selectedSample = $event"
                />
            </q-card>
            <!-- The map takes the rest, but never less than this: on a short screen the page
                 scrolls rather than squeezing the map away. Beside the charts it stretches to their height. -->
            <q-card class="col column" style="min-height: 300px">
                <NiceMap
                    :route-weather="forecast"
                    :selected-sample="selectedSample"
                    @select-sample="selectedSample = $event"
                />
            </q-card>
        </q-card-section>

        <q-inner-loading :showing="forecastLoading && hasGeometry">
            <q-circular-progress
                v-if="forecastProgressPercent !== undefined"
                show-value
                :value="forecastProgressPercent"
                size="3rem"
                :thickness="0.2"
                color="primary"
                track-color="grey-3"
            />
            <q-spinner-dots v-else size="3rem" color="primary" />
            <div class="text-muted q-mt-sm">Wetterdaten werden geladen…</div>
        </q-inner-loading>
    </q-card>
</template>

<style scoped>
.forecast-stacked {
    display: grid;
    grid-template-rows: auto minmax(300px, 1fr);
    grid-template-columns: minmax(0, 1fr);
}
</style>
