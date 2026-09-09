<route lang="json5">
{
  name: "map",
  meta: { title: "Karte" }
}
</route>

<script setup lang="ts">
import NiceMap from "@/components/NiceMap.vue";
import PlaceSearchItem from "@/components/PlaceSearchItem.vue";
import { QSelect } from "quasar";
import { computed, ref, watchEffect } from "vue";
import {
    symSharpDirectionsWalk,
    symSharpElectricBike,
    symSharpElectricCar,
    symSharpElectricMoped,
    symSharpPedalBike,
} from "@quasar/extras/material-symbols-sharp";
import { DefaultApi, type PlacesSearchResult } from "@norain/api";
import type { RecurringRouteOut } from "@norain/api";
import { useQuery } from "@tanstack/vue-query";
import { useRoute } from "vue-router";

const route = useRoute();
const api = new DefaultApi();

const profile = ref("bike");
const departureTime = ref<string>(defaultDepartureTime());

function defaultDepartureTime(): string {
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    // Default to the next full hour, a typical "leaving soon" commute.
    return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours() + 1)}:00`;
}

// Open-Meteo only forecasts ~16 days out, so limit the picker to today .. +15 days.
// Quasar passes each candidate date as "YYYY/MM/DD".
function departureDateOptions(d: string): boolean {
    const pad = (n: number) => String(n).padStart(2, "0");
    const today = new Date();
    const max = new Date();
    max.setDate(max.getDate() + 15);
    const fmt = (x: Date) => `${x.getFullYear()}/${pad(x.getMonth() + 1)}/${pad(x.getDate())}`;
    return d >= fmt(today) && d <= fmt(max);
}

const profiles = [
    { label: "Fuss", value: "foot", icon: symSharpDirectionsWalk },
    { label: "Velo", value: "bike", icon: symSharpPedalBike },
    { label: "E-Bike", value: "ebike", icon: symSharpElectricBike },
    { label: "S-Pedelec", value: "fast_ebike", icon: symSharpElectricMoped },
    { label: "Auto", value: "car", icon: symSharpElectricCar },
];

const routeIdParam = computed(() => (route.query.route as string) || null);

const abfahrtsort = ref<PlacesSearchResult>({
    type: "Feature",
    properties: { name: "Zihlschlacht-Sitterdorf", city: null, state: "Thurgau", countrycode: "CH", showCanton: false },
    geometry: { type: "Point", coordinates: [9.259269150287928, 47.514206200000004] },
});
const zielort = ref<PlacesSearchResult | undefined>(undefined);

// If a routeId query param is present, load the saved route and pre-populate start/dest
const { data: savedRoute } = useQuery({
    queryKey: ["route", routeIdParam],
    queryFn: () => api.coreRoutesApiGetRoute({ routeId: routeIdParam.value! }),
    enabled: () => !!routeIdParam.value,
});

const isViewingSavedRoute = computed(() => !!savedRoute.value);

// Pre-populate start/destination from saved route
watchEffect(() => {
    const r = savedRoute.value;
    if (r) {
        profile.value = r.profile;
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

function usePlaces(filter: ReturnType<typeof ref<string>>) {
    return useQuery({
        queryKey: ["search", filter, mapView],
        enabled: () => !!mapView.value && (filter.value?.length ?? 0) > 2,
        queryFn: () =>
            api.coreApiSearch({
                query: filter.value ?? "",
                zoom: mapView.value?.zoom ?? 12,
                lat: mapView.value?.lat ?? 0,
                lon: mapView.value?.lng ?? 0,
            }),
        initialData: [],
    });
}

const { data: placesStart, isFetching: isFetchingStart } = usePlaces(filterStart);
const { data: placesDest, isFetching: isFetchingDest } = usePlaces(filterDest);

const ready = computed(() => !!zielort.value);

const {
    data: routeWeather,
    isFetching: isFetchingWeather,
    error: weatherError,
} = useQuery({
    queryKey: ["routeWeather", abfahrtsort, zielort, profile, departureTime],
    enabled: ready,
    queryFn: () =>
        api.coreWeatherRouteWeather({
            startLat: abfahrtsort.value.geometry.coordinates[1] ?? 0,
            startLon: abfahrtsort.value.geometry.coordinates[0] ?? 0,
            destLat: zielort.value?.geometry.coordinates[1] ?? 0,
            destLon: zielort.value?.geometry.coordinates[0] ?? 0,
            profile: profile.value,
            departureTime: departureTime.value,
        }),
    staleTime: 5 * 60 * 1000,
});

const summary = computed(() => routeWeather.value?.summary);

function summaryHeadline(): string {
    const s = summary.value;
    if (!s) return "";
    if (!s.willRain) return "Kein Regen erwartet 🎉";
    const t = s.firstRainEta
        ? new Date(s.firstRainEta).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" })
        : "";
    return `Regen wahrscheinlich ab ca. ${t} Uhr 🌧️`;
}

// Risk + "if it rains" amount. When the ensemble is unavailable rainProbability is null, so we drop the
// percentage and show the deterministic amount instead (never a phantom "0%" next to "Regen wahrscheinlich").
function precipText(): string {
    const s = summary.value;
    if (!s) return "";
    if (s.rainProbability == null) {
        return s.rainAmount > 0 ? `bis zu ${s.rainAmount.toFixed(1)} mm Regen` : "trocken";
    }
    const amount = s.rainAmount > 0 ? `bei Regen ~${s.rainAmount.toFixed(1)} mm` : "trocken";
    return `${Math.round(s.rainProbability * 100)}% Regenrisiko · ${amount}`;
}

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

function onMapView(view: { zoom: number; lat: number; lng: number }) {
    mapView.value = view;
}
</script>

<template>
    <q-page class="fit flex justify-center">
        <NiceMap :route-weather="routeWeather" :abfahrtsort="abfahrtsort" :zielort="zielort" @map-view="onMapView">
            <template #search>
                <div class="absolute" style="z-index: 999; width: min(92vw, 420px)">
                    <q-card class="q-pa-md q-mt-md column q-gutter-sm">
                        <q-select
                            v-model="abfahrtsort"
                            label="Abfahrtsort"
                            dense
                            outlined
                            rounded
                            hide-dropdown-icon
                            use-input
                            type="search"
                            :input-debounce="100"
                            :options="placesStart"
                            @filter="onFilterStart"
                        >
                            <template #selected-item="props">
                                <PlaceSearchItem
                                    v-if="props.opt"
                                    :feature="props.opt"
                                    :show-canton="false"
                                    :focused="false"
                                    inline
                                    :clickable="false"
                                />
                            </template>
                            <template #option="props">
                                <PlaceSearchItem
                                    :feature="props.opt"
                                    :focused="props.focused"
                                    :inline="false"
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
                            use-input
                            type="search"
                            :input-debounce="100"
                            :options="placesDest"
                            @filter="onFilterDest"
                        >
                            <template #selected-item="props">
                                <PlaceSearchItem
                                    v-if="props.opt"
                                    :feature="props.opt"
                                    :show-canton="false"
                                    :focused="false"
                                    inline
                                    :clickable="false"
                                />
                            </template>
                            <template #option="props">
                                <PlaceSearchItem
                                    :feature="props.opt"
                                    :focused="props.focused"
                                    :inline="false"
                                    clickable
                                    @click="props.toggleOption(props.opt)"
                                />
                            </template>
                        </q-select>

                        <q-date
                            :model-value="departureTime"
                            label="Abfahrtsdatum"
                            minimal
                            mask="YYYY-MM-DD"
                            :options="departureDateOptions"
                            @update:model-value="val => (departureTime = val + departureTime.slice(10))"
                        />
                        <q-time
                            :model-value="departureTime"
                            format24h
                            label="Abfahrtszeit"
                            now-btn
                            :minute-options="[0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]"
                            @update:model-value="val => (departureTime = departureTime.slice(0, 10) + 'T' + val)"
                        />
                        <!--                        <q-input-->
                        <!--                            v-model="departureTime"-->
                        <!--                            type="datetime-local"-->
                        <!--                            label="Abfahrtszeit"-->
                        <!--                            dense-->
                        <!--                            outlined-->
                        <!--                            stack-label-->
                        <!--                        />-->

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

                        <q-banner v-if="weatherError" dense class="bg-orange-2 text-orange-10 rounded-borders">
                            Route oder Wetter konnte nicht geladen werden. Liegen Start und Ziel innerhalb der geladenen
                            OSM-Region?
                        </q-banner>

                        <q-card-section
                            v-else-if="summary"
                            class="q-pa-sm rounded-borders"
                            :class="summary.willRain ? 'bg-blue-1' : 'bg-green-1'"
                        >
                            <div class="text-subtitle2">{{ summaryHeadline() }}</div>
                            <div class="text-caption">
                                {{ precipText() }} · max. {{ Math.round(summary.maxHeadwind) }} km/h Gegenwind
                                <template v-if="routeWeather">
                                    · {{ Math.round(routeWeather.totalSeconds / 60) }} min ·
                                    {{ (routeWeather.totalDistanceM / 1000).toFixed(1) }} km
                                </template>
                            </div>
                            <div class="text-caption text-grey-7">Quelle: {{ summary.source }}</div>
                        </q-card-section>

                        <div v-else-if="!ready" class="text-caption text-grey-7">
                            Ziel wählen für die Wetterprognose entlang der Route.
                        </div>
                    </q-card>
                </div>
            </template>
        </NiceMap>
    </q-page>
</template>
