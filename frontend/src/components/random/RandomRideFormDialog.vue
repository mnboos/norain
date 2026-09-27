<script setup lang="ts">
import { computed, ref, watch, type Ref } from "vue";
import { QSelect } from "quasar";
import { symSharpElectricBike, symSharpElectricMoped, symSharpPedalBike } from "@quasar/extras/material-symbols-sharp";
import {
    JourneyInKindEnum,
    RoadPrefsInClimbingEnum as Climbing,
    RoadPrefsInSurfaceEnum as Surface,
    RoadPrefsInTownsEnum as Towns,
    RoadPrefsInTrafficEnum as Traffic,
    type JourneyIn,
    type JourneyOut,
    type PlacesSearchResult,
} from "@norain/api/models";
import ChipMultiSelect from "@/components/ChipMultiSelect.vue";
import WeatherRoutingChoice from "@/components/WeatherRoutingChoice.vue";
import PlaceSearchItem from "@/components/PlaceSearchItem.vue";
import RouteLocationPicker from "@/components/RouteLocationPicker.vue";
import { useEntitlements } from "@/composables/useEntitlements";
import { usePlaceSearch } from "@/queries/places";
import { placeLabel } from "@/utils/placeLabel";
import { POI_CATEGORIES } from "@/utils/poiCategories";
import { HEADING_OPTIONS, paceHint } from "@/utils/randomRides";

const props = defineProps<{
    modelValue: boolean;
    /** Edit this random ride instead of creating one. */
    ride?: JourneyOut;
}>();

const emit = defineEmits<{
    "update:modelValue": [value: boolean];
    save: [data: JourneyIn];
}>();

const { weatherRouting } = useEntitlements();

type Length = "time" | "distance";

function place(name: string, lat: number, lon: number): PlacesSearchResult {
    return {
        type: "Feature",
        geometry: { type: "Point", coordinates: [lon, lat] },
        properties: { name, city: null, state: "", countrycode: "", showCanton: false },
    };
}

function today(): string {
    return new Date().toLocaleDateString("sv-SE"); // YYYY-MM-DD, in local time
}

/** The next quarter hour from now, "HH:MM". */
function soon(): string {
    const date = new Date(Date.now() + 15 * 60_000);
    date.setMinutes(Math.ceil(date.getMinutes() / 15) * 15, 0, 0);
    return date.toTimeString().slice(0, 5);
}

const name = ref("");
const start = ref<PlacesSearchResult | null>(null);
const dest = ref<PlacesSearchResult | null>(null);
const roundTrip = ref(true);
const profile = ref("bike");
const date = ref(today());
const departure = ref(soon());
const length = ref<Length>("time");
const hours = ref(2);
const kilometres = ref(40);
const heading = ref<number | null>(null);
const poiCategories = ref<string[]>([]);
const surface = ref(Surface.Any);
const climbing = ref(Climbing.Neutral);
const traffic = ref(Traffic.Neutral);
const towns = ref(Towns.Neutral);
// Every tier: three variants to pick from and save as routes. Plus: weigh them by the weather.
const considerWeather = ref(false);
// Off until the rider chooses it; see WeatherRoutingChoice.
const avoidRain = ref(false);
const avoidHeadwind = ref(false);

function load(ride: JourneyOut | undefined) {
    if (!ride) {
        date.value = today();
        departure.value = soon();
        return;
    }
    name.value = ride.name;
    roundTrip.value = ride.randomPrefs?.roundTrip ?? true;
    heading.value = ride.randomPrefs?.heading ?? null;
    considerWeather.value = ride.randomPrefs?.considerWeather ?? false;
    start.value = place(ride.startName, ride.startLat, ride.startLon);
    dest.value = roundTrip.value ? null : place(ride.destName, ride.destLat, ride.destLon);
    profile.value = ride.profile;
    date.value = ride.startDate.toISOString().slice(0, 10);
    departure.value = ride.earliestStart.slice(0, 5);
    length.value = ride.maxDaySeconds ? "time" : "distance";
    if (ride.maxDaySeconds) hours.value = ride.maxDaySeconds / 3600;
    if (ride.maxDayDistanceM) kilometres.value = ride.maxDayDistanceM / 1000;
    poiCategories.value = [...ride.poiCategories];
    surface.value = ride.roadPrefs.surface ?? Surface.Any;
    climbing.value = ride.roadPrefs.climbing ?? Climbing.Neutral;
    traffic.value = ride.roadPrefs.traffic ?? Traffic.Neutral;
    towns.value = ride.roadPrefs.towns ?? Towns.Neutral;
    avoidRain.value = ride.weatherPrefs.avoidRain ?? false;
    avoidHeadwind.value = ride.weatherPrefs.avoidHeadwind ?? false;
}
watch(
    () => [props.modelValue, props.ride] as const,
    ([open, ride]) => {
        if (open) load(ride);
    },
    { immediate: true },
);

const filterStart = ref("");
const filterDest = ref("");
const defaultSearchLocation = { zoom: 12, lat: 47.5, lon: 9.3 };
const { data: placesStart } = usePlaceSearch(filterStart, defaultSearchLocation);
const { data: placesDest } = usePlaceSearch(filterDest, defaultSearchLocation);

// Quasar QSelect requires doneFn() to signal async filtering is complete (as in RouteFormDialog).
function makeOnFilter(filter: Ref<string>) {
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

function selectInputText(e: Event) {
    if (e.target instanceof HTMLInputElement) e.target.select();
}

const profileOptions = [
    { label: "Velo", value: "bike", icon: symSharpPedalBike },
    { label: "E-Bike", value: "ebike", icon: symSharpElectricBike },
    { label: "S-Pedelec", value: "fast_ebike", icon: symSharpElectricMoped },
];
const lengthOptions = [
    { label: "Zeit", value: "time" },
    { label: "Distanz", value: "distance" },
];
const shapeOptions = [
    { label: "Rundkurs", value: true },
    { label: "Zu einem Ziel", value: false },
];
const surfaceOptions = [
    { label: "Egal", value: Surface.Any },
    { label: "Wenig Naturbelag", value: Surface.AvoidUnpaved },
    { label: "Nur asphaltiert", value: Surface.PavedOnly },
];
// The terrain is the first thing to know about a ride out; it sits beside the profile.
const terrainOptions = [
    { label: "Flach", value: Climbing.Avoid },
    { label: "Egal", value: Climbing.Neutral },
    { label: "Hügelig", value: Climbing.Hilly },
];
const trafficOptions = [
    { label: "Egal", value: Traffic.Neutral },
    { label: "Hauptstrassen meiden", value: Traffic.AvoidMain },
    { label: "Velonetz bevorzugen", value: Traffic.AvoidOffNetwork },
];
const townOptions = [
    { label: "Egal", value: Towns.Neutral },
    { label: "Ortschaften meiden", value: Towns.Avoid },
];

// The weather mode is Plus; without it the ride is always the picker.
const weatherMode = computed(() => weatherRouting.value && considerWeather.value);

const hint = computed(() =>
    length.value === "time"
        ? paceHint(profile.value, { hours: hours.value })
        : paceHint(profile.value, { km: kilometres.value }),
);

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const isValid = computed(
    () =>
        !!start.value &&
        (roundTrip.value || !!dest.value) &&
        /^\d{4}-\d{2}-\d{2}$/.test(date.value) &&
        TIME.test(departure.value) &&
        (length.value === "time" ? hours.value >= 0.5 : kilometres.value >= 5),
);

function onSave() {
    if (!start.value) return;
    const target = roundTrip.value ? start.value : dest.value;
    if (!target) return;
    const data: JourneyIn = {
        name:
            name.value.trim() ||
            (roundTrip.value ? `Runde ab ${start.value.properties.name}` : `Nach ${target.properties.name}`),
        kind: JourneyInKindEnum.Random,
        startLat: start.value.geometry.coordinates[1] ?? 0,
        startLon: start.value.geometry.coordinates[0] ?? 0,
        startName: start.value.properties.name,
        destLat: target.geometry.coordinates[1] ?? 0,
        destLon: target.geometry.coordinates[0] ?? 0,
        destName: target.properties.name,
        viaPoints: [],
        profile: profile.value,
        startDate: new Date(`${date.value}T00:00:00Z`),
        earliestStart: departure.value,
        // The ride is sized by its length, not by an arrival: the day's window stays open.
        latestArrival: "23:59",
        maxDaySeconds: length.value === "time" ? Math.round(hours.value * 3600) : null,
        maxDayDistanceM: length.value === "distance" ? Math.round(kilometres.value * 1000) : null,
        maxLegSeconds: null,
        maxLegDistanceM: null,
        poiCategories: poiCategories.value,
        lodgingKinds: [],
        roadPrefs: { surface: surface.value, climbing: climbing.value, traffic: traffic.value, towns: towns.value },
        randomPrefs: {
            roundTrip: roundTrip.value,
            heading: heading.value,
            considerWeather: weatherMode.value,
        },
        weatherPrefs: {
            avoidRain: weatherMode.value && avoidRain.value,
            avoidHeadwind: weatherMode.value && avoidHeadwind.value,
        },
    };
    emit("save", data);
    emit("update:modelValue", false);
}

function onClose() {
    emit("update:modelValue", false);
}
</script>

<template>
    <q-dialog :model-value="modelValue" persistent :maximized="$q.screen.xs" @update:model-value="onClose">
        <q-card style="min-width: min(640px, 96vw)">
            <q-card-section>
                <q-item-label overline>{{ ride ? "Runde bearbeiten" : "Neue Zufallsrunde" }}</q-item-label>
                <div class="text-caption text-muted">
                    Sag, wie lange oder wie weit du fahren willst. NoRain würfelt eine Strecke und prüft das Wetter
                    darauf.
                </div>
            </q-card-section>

            <q-card-section class="q-gutter-md">
                <q-btn-toggle
                    v-model="roundTrip"
                    :options="shapeOptions"
                    toggle-color="primary"
                    spread
                    no-caps
                    aria-label="Art der Runde"
                />

                <q-select
                    v-model="start"
                    label="Start"
                    dense
                    outlined
                    use-input
                    type="search"
                    hide-dropdown-icon
                    hide-selected
                    fill-input
                    :option-label="placeLabel"
                    :input-debounce="100"
                    :options="placesStart ?? []"
                    @filter="onFilterStart"
                    @focus="selectInputText"
                >
                    <template #option="scope">
                        <PlaceSearchItem
                            :feature="scope.opt"
                            :focused="scope.focused"
                            clickable
                            @click="scope.toggleOption(scope.opt)"
                        />
                    </template>
                </q-select>
                <q-select
                    v-if="!roundTrip"
                    v-model="dest"
                    label="Ziel"
                    dense
                    outlined
                    use-input
                    type="search"
                    hide-dropdown-icon
                    hide-selected
                    fill-input
                    :option-label="placeLabel"
                    :input-debounce="100"
                    :options="placesDest ?? []"
                    @filter="onFilterDest"
                    @focus="selectInputText"
                >
                    <template #option="scope">
                        <PlaceSearchItem
                            :feature="scope.opt"
                            :focused="scope.focused"
                            clickable
                            @click="scope.toggleOption(scope.opt)"
                        />
                    </template>
                </q-select>
                <RouteLocationPicker v-model:start="start" v-model:dest="dest" />

                <div>
                    <div class="text-caption q-mb-sm">Profil (bestimmt das Tempo)</div>
                    <q-btn-toggle v-model="profile" :options="profileOptions" toggle-color="primary" spread size="sm" />
                </div>

                <div>
                    <div class="text-caption q-mb-sm">Gelände</div>
                    <q-btn-toggle
                        v-model="climbing"
                        :options="terrainOptions"
                        toggle-color="primary"
                        spread
                        no-caps
                        size="sm"
                        aria-label="Gelände"
                    />
                    <div class="text-caption text-muted q-mt-xs">
                        <template v-if="climbing === Climbing.Avoid">Möglichst wenig Steigungen.</template>
                        <template v-else-if="climbing === Climbing.Hilly">
                            Lieber auf und ab, so hügelig die Gegend es hergibt.
                        </template>
                        <template v-else>Das Gelände spielt keine Rolle.</template>
                    </div>
                </div>

                <div>
                    <div class="row items-center justify-between">
                        <div class="text-caption">Länge</div>
                        <q-btn-toggle
                            v-model="length"
                            :options="lengthOptions"
                            size="sm"
                            toggle-color="primary"
                            no-caps
                        />
                    </div>
                    <div class="row items-center no-wrap q-gutter-md q-mt-xs">
                        <q-slider
                            v-if="length === 'time'"
                            v-model="hours"
                            class="col"
                            :min="0.5"
                            :max="8"
                            :step="0.25"
                            label
                            :label-value="`${hours} h`"
                            aria-label="Fahrzeit in Stunden"
                        />
                        <q-slider
                            v-else
                            v-model="kilometres"
                            class="col"
                            :min="5"
                            :max="200"
                            :step="5"
                            label
                            :label-value="`${kilometres} km`"
                            aria-label="Distanz in Kilometern"
                        />
                        <div class="text-body2 text-weight-medium" style="min-width: 4rem; text-align: right">
                            {{ length === "time" ? `${hours} h` : `${kilometres} km` }}
                        </div>
                    </div>
                    <div class="text-caption text-muted">{{ hint }}</div>
                </div>

                <q-select
                    v-model="heading"
                    :options="HEADING_OPTIONS"
                    emit-value
                    map-options
                    :label="roundTrip ? 'Richtung zuerst' : 'Umweg auf der Seite'"
                    outlined
                    dense
                    style="max-width: 260px"
                />

                <div class="row q-col-gutter-sm">
                    <q-input v-model="date" class="col-6" type="date" label="Datum" outlined dense />
                    <q-input
                        v-model="departure"
                        class="col-6"
                        label="Abfahrt"
                        outlined
                        dense
                        mask="##:##"
                        fill-mask
                        :error="!TIME.test(departure)"
                        error-message="Als HH:MM"
                        no-error-icon
                    />
                </div>

                <q-input v-model="name" label="Name (optional)" outlined dense no-error-icon />

                <ChipMultiSelect
                    v-model="poiCategories"
                    label="Unterwegs möchte ich vorbei an"
                    hint="Optional: NoRain legt einen Halt auf die Strecke"
                    :options="POI_CATEGORIES.filter(c => c.value !== 'lodging')"
                />

                <div data-testid="random-mode">
                    <div class="row items-center q-gutter-x-sm">
                        <span class="text-caption">Wetter</span>
                        <q-badge v-if="!weatherRouting" color="accent" label="Plus" />
                    </div>
                    <q-toggle
                        :model-value="weatherMode"
                        :disable="!weatherRouting"
                        label="Wetter berücksichtigen"
                        @update:model-value="considerWeather = $event"
                    />
                    <div class="text-caption text-muted">
                        <template v-if="weatherMode">
                            NoRain berechnet die Vorhersage für jede Variante und empfiehlt die mit dem besten Wetter.
                        </template>
                        <template v-else>
                            Du bekommst drei Varianten ohne Wetter und wählst, welche du als Routen speichern willst.
                            Die Vorhersage gibt es dann für jede gespeicherte Route.
                        </template>
                    </div>
                    <WeatherRoutingChoice
                        v-if="weatherMode"
                        v-model:avoid-rain="avoidRain"
                        v-model:avoid-headwind="avoidHeadwind"
                        class="q-mt-sm"
                    />
                </div>

                <q-expansion-item dense label="Strasse" header-class="text-caption q-px-none">
                    <div class="row q-col-gutter-sm q-pt-sm">
                        <q-select
                            v-model="surface"
                            class="col-12 col-sm-6"
                            :options="surfaceOptions"
                            emit-value
                            map-options
                            label="Belag"
                            outlined
                            dense
                        />
                        <q-select
                            v-model="traffic"
                            class="col-12 col-sm-6"
                            :options="trafficOptions"
                            emit-value
                            map-options
                            label="Verkehr"
                            outlined
                            dense
                        />
                        <q-select
                            v-model="towns"
                            class="col-12 col-sm-6"
                            :options="townOptions"
                            emit-value
                            map-options
                            label="Ortschaften"
                            outlined
                            dense
                        />
                    </div>
                </q-expansion-item>
            </q-card-section>

            <q-card-actions align="right">
                <q-btn flat label="Abbrechen" no-caps @click="onClose" />
                <q-btn
                    color="primary"
                    :label="ride ? 'Speichern und neu planen' : 'Varianten würfeln'"
                    :disable="!isValid"
                    no-caps
                    @click="onSave"
                />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
