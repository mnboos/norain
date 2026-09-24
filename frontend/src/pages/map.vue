<route lang="json5">
{
    name: "map",
    meta: { title: "Karte" },
}
</route>

<script setup lang="ts">
import ElevationChart from "@/components/ElevationChart.vue";
import { GeometrySource } from "@norain/api/models";
import { useEntitlements } from "@/composables/useEntitlements";
import RouteLocationPicker from "@/components/RouteLocationPicker.vue";
import DepartureFlexibility from "@/components/DepartureFlexibility.vue";
import DepartureComparison from "@/components/DepartureComparison.vue";
import ForecastSummaryCard from "@/components/ForecastSummaryCard.vue";
import KeyRideDataCard from "@/components/KeyRideDataCard.vue";
import WindDistributionBar from "@/components/WindDistributionBar.vue";
import ForecastDetails from "@/components/ForecastDetails.vue";
import NiceMap from "@/components/NiceMap.vue";
import PlaceSearchItem from "@/components/PlaceSearchItem.vue";
import { placeLabel } from "@/utils/placeLabel";
import { QSelect, useQuasar } from "quasar";
import { useQuery } from "@tanstack/vue-query";
import { useSession } from "@/composables/useSession";
import GpxImportDialog from "@/components/GpxImportDialog.vue";
import RouteTimingFields from "@/components/RouteTimingFields.vue";
import RouteFormDialog from "@/components/RouteFormDialog.vue";
import { gpxApi, gpxError, exportDraft, routePlace, savedRoutePlan, routingProfile, parseDraft, type RouteDraft } from "@/services/gpx";
import type { RoutePlanIn, RecurringRouteIn } from "@norain/api/models";
import { computed, ref, watchEffect, watch } from "vue";
import { symSharpElectricBike, symSharpElectricMoped, symSharpPedalBike } from "@quasar/extras/material-symbols-sharp";
import type { PlacesSearchResult } from "@norain/api/models";
import { useRoute, useRouter } from "vue-router";
import { usePlaceSearch } from "@/queries/places";
import { useRecurringRoute, useCreateRecurringRoute } from "@/queries/recurringRoutes";
import { useRouteWeather } from "@/queries/routeWeather";

const route = useRoute();
const router = useRouter();
const $q = useQuasar();
const { isAuthenticated } = useSession();
const importing = ref(false);
const showSave = ref(false);
const draft = ref<RouteDraft | null>(null);
const duration = ref(0);
const viaPoints = ref<number[][]>([]);
const exporting = ref(false);
const createRoute = useCreateRecurringRoute();
const exact = computed(() => draft.value?.plan.geometrySource === GeometrySource.Imported);
const { isPro } = useEntitlements();

const profile = ref("bike");
const departureTime = ref<string>(defaultDepartureTime());
const flexBefore = ref(0);
const flexAfter = ref(0);
const selectedDeparture = ref<string | null>(null);

function defaultDepartureTime(): string {
    const now = new Date();
    now.setHours(now.getHours() + 1, 0, 0, 0);
    const pad = (n: number) => String(n).padStart(2, "0");
    // Default to the next full hour, a typical "leaving soon" commute.
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:00`;
}

const profiles = [
    { label: "Velo", value: "bike", icon: symSharpPedalBike },
    { label: "E-Bike", value: "ebike", icon: symSharpElectricBike },
    { label: "S-Pedelec", value: "fast_ebike", icon: symSharpElectricMoped },
];

const routeIdParam = computed(() => (typeof route.query.route === "string" ? route.query.route : null));

const abfahrtsort = ref<PlacesSearchResult>({
    type: "Feature",
    properties: { name: "Zihlschlacht-Sitterdorf", city: null, state: "Thurgau", countrycode: "CH", showCanton: false },
    geometry: { type: "Point", coordinates: [9.259269150287928, 47.514206200000004] },
});
const zielort = ref<PlacesSearchResult | undefined>(undefined);

// If a routeId query param is present, load the saved route and pre-populate start/dest
const { data: savedRoute } = useRecurringRoute(routeIdParam);

// Pre-populate start/destination from saved route
watchEffect(() => {
    const r = savedRoute.value;
    if (r) {
        profile.value = r.profile;
        viaPoints.value = r.viaPoints ?? [];
        if (r.geometrySource === GeometrySource.Imported && r.importedCoordinates?.length) {
            draft.value = { plan: savedRoutePlan(r), preview: { coordinates: r.importedCoordinates, distanceM: r.totalDistanceM ?? 0, timeS: r.durationSeconds ?? 1 } };
            duration.value = r.durationSeconds ?? 1;
        } else draft.value = null;
        flexBefore.value = r.departureFlexBeforeMinutes ?? 0;
        flexAfter.value = r.departureFlexAfterMinutes ?? 0;
        abfahrtsort.value = {
            type: "Feature",
            properties: { name: r.startName, city: null, state: "", countrycode: "CH", showCanton: false },
            geometry: { type: "Point", coordinates: [r.startLon, r.startLat] },
        };
        zielort.value = {
            type: "Feature",
            properties: { name: r.destName, city: null, state: "", countrycode: "CH", showCanton: false },
            geometry: { type: "Point", coordinates: [r.destLon, r.destLat] },
        };
        // Pre-populate departure time from saved route's next departure
        if (r.nextDeparture) {
            departureTime.value = r.nextDeparture.slice(0, 16); // YYYY-MM-DDTHH:MM
        }
    }
});

const filterStart = ref("");
const filterDest = ref("");
const mapView = ref<{ zoom: number; lat: number; lng: number } | undefined>(undefined);

const searchLocation = computed(() =>
    mapView.value ? { zoom: mapView.value.zoom, lat: mapView.value.lat, lon: mapView.value.lng } : undefined,
);
const { data: placesStart, isFetching: isFetchingStart } = usePlaceSearch(filterStart, searchLocation);
const { data: placesDest, isFetching: isFetchingDest } = usePlaceSearch(filterDest, searchLocation);
const ready = computed(() => !!zielort.value);

const plan = computed<RoutePlanIn | null>(() => {
    if (!zielort.value) return null;
    return { name: draft.value?.plan.name ?? "NoRain", geometrySource: exact.value ? GeometrySource.Imported : GeometrySource.Graphhopper,
        coordinates: exact.value ? draft.value?.plan.coordinates ?? [] : [abfahrtsort.value.geometry.coordinates, ...viaPoints.value, zielort.value.geometry.coordinates],
        profile: routingProfile(profile.value), durationSeconds: exact.value ? duration.value : null };
});
const validPlan = computed(() => !!plan.value && (!exact.value || (duration.value > 0 && duration.value <= 1382400)));
const previewQuery = useQuery({
    queryKey: computed(() => ["routePlanPreview", plan.value]),
    enabled: validPlan,
    queryFn: ({ signal }) => {
        if (!plan.value) throw new Error("Bitte eine Strecke wählen.");
        return gpxApi.coreApiGpxPreviewGpx({ routePlanIn: plan.value }, { signal });
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
});
const currentDraft = computed<RouteDraft | null>(() => plan.value && previewQuery.data.value ? { plan: plan.value, preview: previewQuery.data.value } : null);
function applyImport(value: RouteDraft) {
    const points = value.plan.coordinates;
    const first = points[0], last = points.at(-1);
    if (!first || !last) return;
    draft.value = value;
    abfahrtsort.value = routePlace(first, "Start");
    zielort.value = routePlace(last, "Ziel");
    viaPoints.value = value.plan.geometrySource === GeometrySource.Graphhopper ? points.slice(1, -1) : [];
    duration.value = value.plan.durationSeconds ?? value.preview.timeS;
    profile.value = value.plan.profile ?? "bike";
}
function clearImport() { draft.value = null; viaPoints.value = []; }
async function saveDraft() {
    if (!currentDraft.value) return;
    if (!isAuthenticated.value) {
        sessionStorage.setItem("norain.plannerDraft", JSON.stringify(currentDraft.value));
        await router.push({ path: "/account", query: { next: "/map?restoreDraft=1" } });
    } else showSave.value = true;
}
if (route.query.restoreDraft === "1") {
    try {
        const value = parseDraft(sessionStorage.getItem("norain.plannerDraft"));
        if (value) { applyImport(value); showSave.value = isAuthenticated.value; }
    } catch { /* A stale browser draft can be discarded. */ }
    sessionStorage.removeItem("norain.plannerDraft");
}
async function saveRoute(data: RecurringRouteIn) {
    try {
        const saved = await createRoute.mutateAsync(data);
        await router.push({ path: "/routes/" + saved.id });
    } catch (e) { $q.notify({ type: "negative", message: await gpxError(e) }); showSave.value = true; }
}
async function exportRoute() {
    if (!currentDraft.value) return;
    exporting.value = true;
    try { await exportDraft(currentDraft.value); }
    catch (e) { $q.notify({ type: "negative", message: await gpxError(e) }); }
    finally { exporting.value = false; }
}
const comparisonQuery = useRouteWeather(abfahrtsort, zielort, profile, departureTime, () => isPro.value ? flexBefore.value : 0, () => isPro.value ? flexAfter.value : 0, validPlan, plan);
const selectedQuery = useRouteWeather(
    abfahrtsort,
    zielort,
    profile,
    () => selectedDeparture.value ?? departureTime.value,
    0,
    0,
    () => selectedDeparture.value !== null && validPlan.value,
    plan,
);
const routeWeather = computed(() => (selectedDeparture.value ? selectedQuery.data.value : comparisonQuery.data.value));
const isFetchingWeather = computed(() =>
    selectedDeparture.value ? selectedQuery.isFetching.value : comparisonQuery.isFetching.value,
);
const weatherError = computed(() =>
    selectedDeparture.value ? selectedQuery.error.value : comparisonQuery.error.value,
);
const departureComparison = computed(() => comparisonQuery.data.value?.departureComparison);
watch(
    [abfahrtsort, zielort, profile, departureTime, flexBefore, flexAfter, plan],
    () => {
        selectedDeparture.value = null;
    },
    { deep: true, flush: "sync" },
);

const selectedSample = ref(0);
watch(routeWeather, () => {
    selectedSample.value = 0;
});

function makeOnFilter(filter: ReturnType<typeof ref<string>>) {
    return (val: string, doneFn: (cb: () => void, after?: (ref: QSelect) => void) => void) => {
        doneFn(
            () => {
                filter.value = val;
            },
            ref => {
                if (val !== "" && !!ref.options?.length && ref.getOptionIndex() === -1) {
                    ref.moveOptionSelection(1, true);
                }
            },
        );
    };
}

const onFilterStart = makeOnFilter(filterStart);
const onFilterDest = makeOnFilter(filterDest);

// Select the chosen place's name on focus, so typing replaces it instead of appending to it.
function selectInputText(e: Event) {
    if (e.target instanceof HTMLInputElement) e.target.select();
}

function onMapView(view: { zoom: number; lat: number; lng: number }) {
    mapView.value = view;
}
</script>

<template>
    <q-page class="fit flex justify-center">
        <GpxImportDialog v-model="importing" :profile="profile" @apply="applyImport" />
        <RouteFormDialog v-model="showSave" :initial-draft="currentDraft ?? draft" @save="saveRoute" />
        <NiceMap
            :route-weather="routeWeather"
            :preview-line="previewQuery.data.value?.coordinates"
            :abfahrtsort="abfahrtsort"
            :zielort="zielort"
            :selected-sample="selectedSample"
            @select-sample="selectedSample = $event"
            @map-view="onMapView"
        >
            <template #search>
                <div
                    class="absolute"
                    style="z-index: 999; width: min(92vw, 420px); max-height: calc(100dvh - 60px); overflow-y: auto"
                >
                    <q-card class="q-pa-md q-mt-md q-gutter-y-sm">
                        <div class="row q-gutter-xs">
                            <q-btn flat no-caps label="GPX importieren" @click="importing = true" />
                            <q-btn flat no-caps label="GPX exportieren" :disable="!currentDraft" :loading="exporting" @click="exportRoute" />
                            <q-btn flat no-caps label="Route speichern" :disable="!currentDraft" @click="saveDraft" />
                        </div>
                        <template v-if="draft">
                            <div class="text-subtitle2">{{ draft.plan.name }}</div>
                            <RouteTimingFields v-if="exact" v-model="duration" :distance-m="draft.preview.distanceM" />
                            <q-btn flat dense no-caps label="Neue Route planen" @click="clearImport" />
                        </template>
                        <div v-if="previewQuery.error.value" class="text-negative" role="alert">Die Strecke konnte nicht berechnet werden.</div>
                        <q-select
                            v-model="abfahrtsort"
                            :disable="exact"
                            label="Abfahrtsort"
                            dense
                            outlined
                            rounded
                            hide-dropdown-icon
                            hide-selected
                            fill-input
                            :option-label="placeLabel"
                            use-input
                            type="search"
                            :input-debounce="100"
                            :options="placesStart"
                            @filter="onFilterStart"
                            @focus="selectInputText"
                        >
                            <template #option="props">
                                <PlaceSearchItem
                                    :feature="props.opt"
                                    :focused="props.focused"
                                    clickable
                                    @click="props.toggleOption(props.opt)"
                                />
                            </template>
                        </q-select>

                        <q-select
                            v-model="zielort"
                            :disable="exact"
                            label="Zielort"
                            dense
                            outlined
                            rounded
                            hide-dropdown-icon
                            hide-selected
                            fill-input
                            :option-label="placeLabel"
                            use-input
                            type="search"
                            :input-debounce="100"
                            :options="placesDest"
                            @filter="onFilterDest"
                            @focus="selectInputText"
                        >
                            <template #option="props">
                                <PlaceSearchItem
                                    :feature="props.opt"
                                    :focused="props.focused"
                                    clickable
                                    @click="props.toggleOption(props.opt)"
                                />
                            </template>
                        </q-select>

                        <RouteLocationPicker
v-if="!exact"
                            :start="abfahrtsort"
                            :dest="zielort"
                            @update:start="
                                value => {
                                    if (value) abfahrtsort = value;
                                }
                            "
                            @update:dest="
                                value => {
                                    zielort = value ?? undefined;
                                }
                            "
                        />
                        <q-input
                            v-model="departureTime"
                            type="datetime-local"
                            label="Abfahrtszeit"
                            dense
                            outlined
                            stack-label
                        />
                        <ElevationChart
v-if="previewQuery.data.value"
                            :coordinates="previewQuery.data.value.coordinates"
                            :total-seconds="previewQuery.data.value.timeS"
                            :vertex-times="previewQuery.data.value.vertexTimes" />
                        <DepartureFlexibility v-model:before="flexBefore" v-model:after="flexAfter" />

                        <q-btn-toggle
                            v-model="profile"
                            unelevated
                            text-color="primary"
                            no-caps
                            spread
                            dense
                            rounded
                            :options="profiles"
                        />

                        <q-linear-progress
                            v-if="isFetchingWeather || isFetchingStart || isFetchingDest"
                            indeterminate
                            class="q-mt-xs"
                        />

                        <DepartureComparison
                            v-if="departureComparison"
                            :comparison="departureComparison"
                            :selected-time="selectedDeparture"
                            @select="selectedDeparture = $event"
                            @reset="selectedDeparture = null"
                        />

                        <q-banner v-if="weatherError" dense class="bg-tint-warn rounded-borders">
                            Route oder Wetter konnte nicht geladen werden. Liegen Start und Ziel innerhalb der geladenen
                            OSM-Region?
                        </q-banner>

                        <template v-else-if="routeWeather">
                            <ForecastSummaryCard flat :forecast="routeWeather" />
                            <KeyRideDataCard flat :forecast="routeWeather" :columns="2" />
                            <template v-if="routeWeather.summary.windDistribution">
                                <div class="text-subtitle2">Wind entlang der Strecke</div>
                                <WindDistributionBar :distribution="routeWeather.summary.windDistribution" />
                            </template>
                            <ForecastDetails v-model:selected-sample="selectedSample" :forecast="routeWeather" />
                        </template>

                        <div v-else-if="!ready" class="text-caption text-muted">
                            Ziel wählen für die Wetterprognose entlang der Route.
                        </div>
                    </q-card>
                </div>
            </template>
        </NiceMap>
    </q-page>
</template>
