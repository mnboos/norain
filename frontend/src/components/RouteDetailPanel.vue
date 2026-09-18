<script setup lang="ts">
import { computed, ref, toRefs, watch } from "vue";
import { symSharpCloudOff } from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import DepartureFlexibility from "@/components/DepartureFlexibility.vue";
import DepartureComparison from "@/components/DepartureComparison.vue";
import WeatherSummaryCard from "@/components/WeatherSummaryCard.vue";
import WeatherCharts from "@/components/WeatherCharts.vue";
import NiceMap from "@/components/NiceMap.vue";
import { useRecurringRoute, useRecurringRouteForecast, useUpdateRecurringRoute } from "@/queries/recurringRoutes";

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

const flexBefore = ref(route.value.departureFlexBeforeMinutes ?? 0);
const flexAfter = ref(route.value.departureFlexAfterMinutes ?? 0);
const selectedDeparture = ref<string | null>(null);
watch(
    () => [route.value.id, route.value.departureFlexBeforeMinutes, route.value.departureFlexAfterMinutes],
    () => {
        flexBefore.value = route.value.departureFlexBeforeMinutes ?? 0;
        flexAfter.value = route.value.departureFlexAfterMinutes ?? 0;
    },
);
watch(
    [routeId, departureDate, departureTime, flexBefore, flexAfter],
    () => {
        selectedDeparture.value = null;
    },
    { flush: "sync" },
);
const comparisonQuery = useRecurringRouteForecast(
    routeId,
    departureDate,
    departureTime,
    () => hasGeometry.value && !!departureDate.value && !!departureTime.value,
    flexBefore,
    flexAfter,
);
const selectedQuery = useRecurringRouteForecast(
    routeId,
    () => selectedDeparture.value?.slice(0, 10) ?? departureDate.value,
    () => selectedDeparture.value?.slice(11) ?? departureTime.value,
    () => hasGeometry.value && selectedDeparture.value !== null,
    0,
    0,
);
const forecast = computed(() => (selectedDeparture.value ? selectedQuery.data.value : comparisonQuery.data.value));
const forecastLoading = computed(() =>
    selectedDeparture.value ? selectedQuery.isFetching.value : comparisonQuery.isFetching.value,
);
const forecastError = computed(() =>
    selectedDeparture.value ? selectedQuery.error.value : comparisonQuery.error.value,
);
const forecastProgress = computed(() =>
    selectedDeparture.value ? selectedQuery.progress.value : comparisonQuery.progress.value,
);
const departureComparison = computed(() => comparisonQuery.data.value?.departureComparison);
const saveWindow = useUpdateRecurringRoute();
const windowChanged = computed(
    () =>
        flexBefore.value !== (route.value.departureFlexBeforeMinutes ?? 0) ||
        flexAfter.value !== (route.value.departureFlexAfterMinutes ?? 0),
);
function saveFlexibility() {
    saveWindow.mutate({
        id: route.value.id,
        data: {
            ...route.value,
            departureFlexBeforeMinutes: flexBefore.value,
            departureFlexAfterMinutes: flexAfter.value,
        },
    });
}

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
        <q-card-section>
            <DepartureFlexibility v-model:before="flexBefore" v-model:after="flexAfter">
                <q-btn
                    v-if="windowChanged"
                    flat
                    no-caps
                    label="Für diese Route speichern"
                    :loading="saveWindow.isPending.value"
                    @click="saveFlexibility"
                />
                <div v-if="saveWindow.isError.value" role="alert">Zeitfenster konnte nicht gespeichert werden.</div>
            </DepartureFlexibility>
            <DepartureComparison
                v-if="departureComparison"
                :comparison="departureComparison"
                :selected-time="selectedDeparture"
                @select="selectedDeparture = $event"
                @reset="selectedDeparture = null"
            />
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
        <q-card-section v-if="forecast" class="no-padding col forecast-stacked">
            <q-card class="column forecast-map">
                <NiceMap
                    :route-weather="forecast"
                    :selected-sample="selectedSample"
                    @select-sample="selectedSample = $event"
                />
            </q-card>
            <q-card flat class="transparent forecast-charts">
                <WeatherCharts
                    :job-id="forecast.jobId"
                    :version="forecast.version"
                    :selected-sample="selectedSample"
                    :samples="forecast.samples"
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
    grid-template-rows: minmax(240px, 1fr) clamp(180px, 25dvh, 260px);
    gap: 4px;
    grid-template-columns: minmax(0, 1fr);
}
.forecast-map,
.forecast-charts {
    min-width: 0;
    min-height: 0;
}
</style>
