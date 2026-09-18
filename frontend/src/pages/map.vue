<route lang="json5">
{
    name: "map",
    meta: { title: "Karte" },
}
</route>

<script setup lang="ts">
import { useEntitlements } from "@/composables/useEntitlements";
import RouteLocationPicker from "@/components/RouteLocationPicker.vue";
import DepartureFlexibility from "@/components/DepartureFlexibility.vue";
import DepartureComparison from "@/components/DepartureComparison.vue";
import WeatherSummaryCard from "@/components/WeatherSummaryCard.vue";
import ForecastDetails from "@/components/ForecastDetails.vue";
import NiceMap from "@/components/NiceMap.vue";
import PlaceSearchItem from "@/components/PlaceSearchItem.vue";
import { placeLabel } from "@/utils/placeLabel";
import { QSelect } from "quasar";
import { computed, ref, watchEffect, watch } from "vue";
import { symSharpElectricBike, symSharpElectricMoped, symSharpPedalBike } from "@quasar/extras/material-symbols-sharp";
import type { PlacesSearchResult } from "@norain/api/models";
import { useRoute } from "vue-router";
import { usePlaceSearch } from "@/queries/places";
import { useRecurringRoute } from "@/queries/recurringRoutes";
import { useRouteWeather } from "@/queries/routeWeather";

const route = useRoute();
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

const comparisonQuery = useRouteWeather(abfahrtsort, zielort, profile, departureTime, () => isPro.value ? flexBefore.value : 0, () => isPro.value ? flexAfter.value : 0);
const selectedQuery = useRouteWeather(
    abfahrtsort,
    zielort,
    profile,
    () => selectedDeparture.value ?? departureTime.value,
    0,
    0,
    () => selectedDeparture.value !== null,
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
    [abfahrtsort, zielort, profile, departureTime, flexBefore, flexAfter],
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
        <NiceMap
            :route-weather="routeWeather"
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
                        <q-select
                            v-model="abfahrtsort"
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
                            <WeatherSummaryCard :forecast="routeWeather" />
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
