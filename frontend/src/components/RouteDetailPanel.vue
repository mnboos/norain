<script setup lang="ts">
import { GeometrySource } from "@norain/api/models";
import { computed, ref, toRefs, watch } from "vue";
import { useQuasar } from "quasar";
import {
    symSharpCloudOff,
    symSharpPedalBike,
    symSharpEditRoad,
    symSharpDownload,
} from "@quasar/extras/material-symbols-sharp";
import type { RecurringRouteOut } from "@norain/api/models";
import { useEntitlements } from "@/composables/useEntitlements";
import DepartureFlexibility from "@/components/DepartureFlexibility.vue";
import DepartureComparison from "@/components/DepartureComparison.vue";
import ForecastSummaryCard from "@/components/ForecastSummaryCard.vue";
import KeyRideDataCard from "@/components/KeyRideDataCard.vue";
import WindDistributionBar from "@/components/WindDistributionBar.vue";
import WeatherChart from "@/components/WeatherChart.vue";
import NiceMap from "@/components/NiceMap.vue";
import RouteEditorDialog from "@/components/RouteEditorDialog.vue";
import RouteTimingFields from "@/components/RouteTimingFields.vue";
import { gpxApi, downloadGpx, gpxError } from "@/services/gpx";
import { toLonLat, type LonLat } from "@/utils/routeEditing";
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
const $q = useQuasar();
const { isPro } = useEntitlements();

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
    () => (isPro.value ? flexBefore.value : 0),
    () => (isPro.value ? flexAfter.value : 0),
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

// Reshaping is done on the outbound route; the server mirrors its via points onto the return.
const editing = ref(false);
const exporting = ref(false);
const duration = ref(route.value.durationSeconds ?? 0);
watch(
    () => route.value.durationSeconds,
    value => {
        duration.value = value ?? 0;
    },
);
async function exportRoute() {
    exporting.value = true;
    try {
        const response = await gpxApi.coreApiGpxExportSavedGpxRaw({ routeId: route.value.id });
        await downloadGpx(response.raw, route.value.name);
    } catch (e) {
        $q.notify({ type: "negative", message: await gpxError(e) });
    } finally {
        exporting.value = false;
    }
}
function saveDuration() {
    saveShape.mutate(
        { id: route.value.id, data: { ...route.value, durationSeconds: duration.value } },
        {
            onError: e => {
                void gpxError(e).then(message => {
                    $q.notify({ type: "negative", message });
                });
            },
        },
    );
}
const saveShape = useUpdateRecurringRoute();
const canEditShape = computed(
    () => !route.value.parentRouteId && route.value.geometrySource !== GeometrySource.Imported,
);
const viaPoints = computed(() => (route.value.viaPoints ?? []).map(toLonLat));
function saveViaPoints(points: LonLat[]) {
    saveShape.mutate(
        { id: route.value.id, data: { ...route.value, viaPoints: points } },
        { onError: () => $q.notify({ type: "negative", message: "Die Strecke konnte nicht gespeichert werden." }) },
    );
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
    <!-- Two parts share the height: the cards take what they need, the map fills the rest. -->
    <div class="relative-position column">
        <div class="row q-col-gutter-md col-auto">
            <div class="col-12 col-sm-6 col-md-2">
                <q-card class="full-height">
                    <q-item class="q-pt-md">
                        <!--                        <q-item-section side>-->
                        <!--                            <slot name="back" />-->
                        <!--                        </q-item-section>-->
                        <!-- Left out where this card is at its narrowest, so the name keeps its room. -->
                        <!--                        <q-item-section v-if="$q.screen.width >= 1280 || $q.screen.lt.md" avatar>-->
                        <!--                            <q-avatar rounded class="bg-tint-wet" text-color="primary" :icon="symSharpMap" />-->
                        <!--                        </q-item-section>-->
                        <q-item-section>
                            <q-item-label>
                                <h1 class="text-h6 text-weight-bold q-ma-none">{{ route.name }}</h1>
                            </q-item-label>
                        </q-item-section>
                    </q-item>
                    <q-card-section class="q-pt-none text-caption text-muted">
                        <div>{{ route.startName }} → {{ route.destName }}</div>
                        <div>{{ route.scheduleDescription }}</div>
                        <div v-if="route.description">{{ route.description }}</div>
                        <q-chip
                            dense
                            outline
                            color="primary"
                            :icon="symSharpPedalBike"
                            :label="profileLabel"
                            class="q-ml-none q-mt-sm"
                        />
                        <q-btn
                            v-if="canEditShape"
                            flat
                            dense
                            no-caps
                            :icon="symSharpEditRoad"
                            color="primary"
                            label="Strecke anpassen"
                            class="q-mt-sm"
                            :loading="saveShape.isPending.value"
                            @click="editing = true"
                        />
                        <q-btn
                            flat
                            dense
                            no-caps
                            :icon="symSharpDownload"
                            label="GPX exportieren"
                            :disable="!hasGeometry && route.geometrySource !== 'imported'"
                            :loading="exporting"
                            @click="exportRoute"
                        />
                        <template v-if="route.geometrySource === 'imported' && !route.parentRouteId">
                            <div class="q-my-sm">Originalstrecke aus GPX</div>
                            <RouteTimingFields v-model="duration" :distance-m="route.totalDistanceM ?? 0" />
                            <q-btn
                                flat
                                no-caps
                                label="Fahrzeit speichern"
                                :disable="duration <= 0 || duration > 1382400 || duration === route.durationSeconds"
                                :loading="saveShape.isPending.value"
                                @click="saveDuration"
                            />
                        </template>
                        <RouteEditorDialog
                            v-if="canEditShape"
                            v-model="editing"
                            :start="[route.startLon, route.startLat]"
                            :dest="[route.destLon, route.destLat]"
                            :profile="route.profile"
                            :via-points="viaPoints"
                            @apply="saveViaPoints"
                        />
                    </q-card-section>
                    <q-separator inset />
                    <DepartureFlexibility v-model:before="flexBefore" v-model:after="flexAfter">
                        <q-btn
                            v-if="windowChanged"
                            flat
                            no-caps
                            label="Für diese Route speichern"
                            :loading="saveWindow.isPending.value"
                            @click="saveFlexibility"
                        />
                        <div v-if="saveWindow.isError.value" role="alert">
                            Zeitfenster konnte nicht gespeichert werden.
                        </div>
                    </DepartureFlexibility>
                    <q-card-section v-if="departureComparison">
                        <DepartureComparison
                            :comparison="departureComparison"
                            :selected-time="selectedDeparture"
                            @select="selectedDeparture = $event"
                            @reset="selectedDeparture = null"
                        />
                    </q-card-section>
                </q-card>
            </div>

            <template v-if="forecast">
                <div class="col-12 col-sm-6 col-md-4">
                    <!-- The key figures sit under the forecast, in the same card. -->
                    <q-card class="full-height column">
                        <ForecastSummaryCard flat :forecast="forecast" class="col" />
                        <q-separator />
                        <KeyRideDataCard flat :forecast="forecast" :columns="$q.screen.width >= 1280 ? 4 : 2" />
                    </q-card>
                </div>
                <div class="col-12 col-sm-6 col-md-3">
                    <q-card class="full-height">
                        <q-card-section class="q-pb-none">
                            <div class="text-subtitle2 q-mb-xs">Wind entlang der Strecke</div>
                            <WindDistributionBar
                                v-if="forecast.summary.windDistribution"
                                :distribution="forecast.summary.windDistribution"
                            />
                        </q-card-section>
                        <q-card-section style="height: 260px" class="q-pa-none">
                            <WeatherChart
                                kind="headwind"
                                :version="forecast.version"
                                :selected-sample="selectedSample"
                                :samples="forecast.samples"
                                @select-sample="selectedSample = $event"
                            />
                        </q-card-section>
                    </q-card>
                </div>

                <div class="col-12 col-sm-6 col-md-3">
                    <q-card class="full-height column">
                        <q-card-section class="col q-pa-none" style="min-height: 300px">
                            <WeatherChart
                                kind="temperature"
                                :version="forecast.version"
                                :selected-sample="selectedSample"
                                :samples="forecast.samples"
                                @select-sample="selectedSample = $event"
                            />
                        </q-card-section>
                    </q-card>
                </div>
            </template>

            <div v-if="!hasGeometry || forecastError || (!route.forecastAvailable && !forecastLoading)" class="col-12">
                <q-banner v-if="!hasGeometry" rounded class="bg-tint-warn">
                    <template #avatar>
                        <q-spinner-dots size="1.5rem" color="accent" />
                    </template>
                    Route wird berechnet...
                </q-banner>
                <q-banner v-else-if="forecastError" rounded class="bg-tint-error">
                    Wetterdaten konnten nicht geladen werden. Ist diese Route nach Ablauf von Plus pausiert?
                    <q-btn flat to="/account" label="Aktive Routen und Tarif verwalten" no-caps />
                </q-banner>
                <q-banner v-else rounded class="bg-tint-neutral">
                    <template #avatar>
                        <q-icon :name="symSharpCloudOff" class="text-muted" />
                    </template>
                    Noch keine Vorhersage möglich. Die Wettervorhersage ist erst näher am Abfahrtstermin verfügbar.
                </q-banner>
            </div>
        </div>

        <!-- On phones the cards stack and the map keeps a fixed height; on a wide screen it
             fills what the cards leave, but never less than 300 px. -->
        <div v-if="forecast" class="col column q-mt-md" :style="$q.screen.lt.md ? undefined : 'min-height: 300px'">
            <q-card class="col column overflow-hidden">
                <NiceMap
                    :route-weather="forecast"
                    :selected-sample="selectedSample"
                    :height="$q.screen.lt.md ? '45vh' : undefined"
                    @select-sample="selectedSample = $event"
                />
            </q-card>
        </div>

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
    </div>
</template>
