<script setup lang="ts">
import { computed, ref, watch, type Ref } from "vue";
import { QSelect } from "quasar";
import {
    symSharpElectricBike,
    symSharpElectricMoped,
    symSharpPedalBike,
} from "@quasar/extras/material-symbols-sharp";
import {
    RoadPrefsInClimbingEnum as Climbing,
    RoadPrefsInSurfaceEnum as Surface,
    RoadPrefsInTownsEnum as Towns,
    RoadPrefsInTrafficEnum as Traffic,
    type JourneyIn,
    type JourneyOut,
    type PlacesSearchResult,
} from "@norain/api/models";
import PlaceSearchItem from "@/components/PlaceSearchItem.vue";
import RouteLocationPicker from "@/components/RouteLocationPicker.vue";
import { useEntitlements } from "@/composables/useEntitlements";
import { usePlaceSearch } from "@/queries/places";
import { placeLabel } from "@/utils/placeLabel";
import { LODGING_KINDS, POI_CATEGORIES } from "@/utils/poiCategories";
import ChipMultiSelect from "@/components/ChipMultiSelect.vue";

const props = defineProps<{
    modelValue: boolean;
    /** Edit this journey instead of creating one. */
    journey?: JourneyOut;
}>();

const emit = defineEmits<{
    "update:modelValue": [value: boolean];
    save: [data: JourneyIn];
}>();

const { isPro } = useEntitlements();

type Limit = "distance" | "time";

function place(name: string, lat: number, lon: number): PlacesSearchResult {
    return {
        type: "Feature",
        geometry: { type: "Point", coordinates: [lon, lat] },
        properties: { name, city: null, state: "", countrycode: "", showCanton: false },
    };
}

function tomorrow(): string {
    const date = new Date();
    date.setDate(date.getDate() + 1);
    return date.toLocaleDateString("sv-SE"); // YYYY-MM-DD, in local time
}

const name = ref("");
const start = ref<PlacesSearchResult | null>(null);
const dest = ref<PlacesSearchResult | null>(null);
const profile = ref("bike");
const startDate = ref(tomorrow());
const earliestStart = ref("08:00");
const latestArrival = ref("18:00");
const dayLimit = ref<Limit>("distance");
const dayKm = ref(80);
const dayHours = ref(5);
const legLimit = ref<Limit>("distance");
const legKm = ref(25);
const legHours = ref(1.5);
const poiCategories = ref<string[]>(["drinking_water", "toilets"]);
const lodgingKinds = ref<string[]>(["camp_site", "hostel", "guest_house", "hotel"]);
const surface = ref(Surface.Any);
const climbing = ref(Climbing.Neutral);
const traffic = ref(Traffic.Neutral);
const towns = ref(Towns.Neutral);
const avoidRain = ref(true);
const avoidHeadwind = ref(true);
const departureWindow = ref(60);

function load(journey: JourneyOut | undefined) {
    if (!journey) return;
    name.value = journey.name;
    start.value = place(journey.startName, journey.startLat, journey.startLon);
    dest.value = place(journey.destName, journey.destLat, journey.destLon);
    profile.value = journey.profile;
    startDate.value = journey.startDate.toISOString().slice(0, 10);
    earliestStart.value = journey.earliestStart.slice(0, 5);
    latestArrival.value = journey.latestArrival.slice(0, 5);
    dayLimit.value = journey.maxDayDistanceM ? "distance" : "time";
    if (journey.maxDayDistanceM) dayKm.value = journey.maxDayDistanceM / 1000;
    if (journey.maxDaySeconds) dayHours.value = journey.maxDaySeconds / 3600;
    legLimit.value = journey.maxLegSeconds && !journey.maxLegDistanceM ? "time" : "distance";
    if (journey.maxLegDistanceM) legKm.value = journey.maxLegDistanceM / 1000;
    if (journey.maxLegSeconds) legHours.value = journey.maxLegSeconds / 3600;
    poiCategories.value = [...journey.poiCategories];
    lodgingKinds.value = [...journey.lodgingKinds];
    surface.value = journey.roadPrefs.surface ?? Surface.Any;
    climbing.value = journey.roadPrefs.climbing ?? Climbing.Neutral;
    traffic.value = journey.roadPrefs.traffic ?? Traffic.Neutral;
    towns.value = journey.roadPrefs.towns ?? Towns.Neutral;
    avoidRain.value = journey.weatherPrefs.avoidRain ?? true;
    avoidHeadwind.value = journey.weatherPrefs.avoidHeadwind ?? true;
    departureWindow.value = journey.weatherPrefs.departureWindowMinutes ?? 60;
}
watch(
    () => [props.modelValue, props.journey] as const,
    ([open, journey]) => {
        if (open) load(journey);
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

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const timesValid = computed(
    () => TIME.test(earliestStart.value) && TIME.test(latestArrival.value) && latestArrival.value > earliestStart.value,
);

const profileOptions = [
    { label: "Velo", value: "bike", icon: symSharpPedalBike },
    { label: "E-Bike", value: "ebike", icon: symSharpElectricBike },
    { label: "S-Pedelec", value: "fast_ebike", icon: symSharpElectricMoped },
];
const limitOptions = [
    { label: "km", value: "distance" },
    { label: "Stunden", value: "time" },
];
const surfaceOptions = [
    { label: "Egal", value: Surface.Any },
    { label: "Wenig Naturbelag", value: Surface.AvoidUnpaved },
    { label: "Nur asphaltiert", value: Surface.PavedOnly },
];
const climbingOptions = [
    { label: "Egal", value: Climbing.Neutral },
    { label: "Steigungen meiden", value: Climbing.Avoid },
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
const windowOptions = [0, 30, 60, 90, 120].map(value => ({
    label: value ? `bis ${value} min später` : "Fix",
    value,
}));

const isValid = computed(
    () =>
        !!name.value.trim() &&
        !!start.value &&
        !!dest.value &&
        /^\d{4}-\d{2}-\d{2}$/.test(startDate.value) &&
        timesValid.value &&
        (dayLimit.value === "distance" ? dayKm.value >= 5 : dayHours.value >= 0.5) &&
        (legLimit.value === "distance" ? legKm.value >= 2 : legHours.value >= 0.2),
);

function onSave() {
    if (!start.value || !dest.value) return;
    const data: JourneyIn = {
        name: name.value.trim(),
        startLat: start.value.geometry.coordinates[1] ?? 0,
        startLon: start.value.geometry.coordinates[0] ?? 0,
        startName: start.value.properties.name,
        destLat: dest.value.geometry.coordinates[1] ?? 0,
        destLon: dest.value.geometry.coordinates[0] ?? 0,
        destName: dest.value.properties.name,
        viaPoints: props.journey?.viaPoints ?? [],
        profile: profile.value,
        startDate: new Date(`${startDate.value}T00:00:00Z`),
        earliestStart: earliestStart.value,
        latestArrival: latestArrival.value,
        maxDayDistanceM: dayLimit.value === "distance" ? Math.round(dayKm.value * 1000) : null,
        maxDaySeconds: dayLimit.value === "time" ? Math.round(dayHours.value * 3600) : null,
        maxLegDistanceM: legLimit.value === "distance" ? Math.round(legKm.value * 1000) : null,
        maxLegSeconds: legLimit.value === "time" ? Math.round(legHours.value * 3600) : null,
        poiCategories: poiCategories.value,
        lodgingKinds: lodgingKinds.value,
        roadPrefs: { surface: surface.value, climbing: climbing.value, traffic: traffic.value, towns: towns.value },
        weatherPrefs: {
            avoidRain: avoidRain.value,
            avoidHeadwind: avoidHeadwind.value,
            departureWindowMinutes: departureWindow.value,
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
        <q-card style="min-width: min(720px, 96vw)">
            <q-card-section>
                <q-item-label overline>{{ journey ? "Reise bearbeiten" : "Neue Reise" }}</q-item-label>
                <div class="text-caption text-muted">
                    NoRain schlägt Tagesetappen, Pausen und Abfahrtszeiten vor, nach Wetter und deinen Wünschen.
                </div>
            </q-card-section>

            <q-card-section class="q-gutter-md">
                <q-input v-model="name" label="Name" outlined dense autofocus no-error-icon />

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
                    <div class="text-caption q-mb-sm">Profil</div>
                    <q-btn-toggle v-model="profile" :options="profileOptions" toggle-color="primary" spread size="sm" />
                </div>

                <div class="row q-col-gutter-sm">
                    <q-input v-model="startDate" class="col-12 col-sm-4" type="date" label="Erster Tag" outlined dense />
                    <q-input
                        v-model="earliestStart"
                        class="col-6 col-sm-4"
                        label="Frühester Start"
                        outlined
                        dense
                        mask="##:##"
                        fill-mask
                    />
                    <q-input
                        v-model="latestArrival"
                        class="col-6 col-sm-4"
                        label="Späteste Ankunft"
                        outlined
                        dense
                        mask="##:##"
                        fill-mask
                        :error="!timesValid"
                        error-message="Ankunft nach dem Start, als HH:MM"
                        no-error-icon
                    />
                </div>

                <div class="row q-col-gutter-sm items-center">
                    <div class="col-12 col-sm-6">
                        <div class="text-caption q-mb-xs">Pro Tag höchstens</div>
                        <div class="row no-wrap items-center q-gutter-sm">
                            <q-input
                                v-if="dayLimit === 'distance'"
                                v-model.number="dayKm"
                                type="number"
                                min="5"
                                outlined
                                dense
                                suffix="km"
                                style="width: 120px"
                            />
                            <q-input
                                v-else
                                v-model.number="dayHours"
                                type="number"
                                min="0.5"
                                step="0.5"
                                outlined
                                dense
                                suffix="h"
                                style="width: 120px"
                            />
                            <q-btn-toggle v-model="dayLimit" :options="limitOptions" size="sm" toggle-color="primary" />
                        </div>
                    </div>
                    <div class="col-12 col-sm-6">
                        <div class="text-caption q-mb-xs">Am Stück (bis zur nächsten Pause)</div>
                        <div class="row no-wrap items-center q-gutter-sm">
                            <q-input
                                v-if="legLimit === 'distance'"
                                v-model.number="legKm"
                                type="number"
                                min="2"
                                outlined
                                dense
                                suffix="km"
                                style="width: 120px"
                            />
                            <q-input
                                v-else
                                v-model.number="legHours"
                                type="number"
                                min="0.25"
                                step="0.25"
                                outlined
                                dense
                                suffix="h"
                                style="width: 120px"
                            />
                            <q-btn-toggle v-model="legLimit" :options="limitOptions" size="sm" toggle-color="primary" />
                        </div>
                    </div>
                </div>

                <ChipMultiSelect
                    v-model="poiCategories"
                    label="Auf jeder Etappe am Stück brauche ich"
                    hint="Jede Auswahl muss auf jeder Etappe vorkommen"
                    :options="POI_CATEGORIES.filter(c => c.value !== 'lodging')"
                />

                <ChipMultiSelect v-model="lodgingKinds" label="Übernachten in" hint="Leer: jede Art" :options="LODGING_KINDS" />

                <q-expansion-item dense label="Strasse" header-class="text-caption q-px-none" default-opened>
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
                            v-model="climbing"
                            class="col-12 col-sm-6"
                            :options="climbingOptions"
                            emit-value
                            map-options
                            label="Steigungen"
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

                <q-expansion-item dense label="Wetter" header-class="text-caption q-px-none" default-opened>
                    <div class="q-pt-sm">
                        <q-toggle v-model="avoidRain" label="Um Regen herum planen" />
                        <q-toggle v-model="avoidHeadwind" label="Starken Gegenwind meiden" />
                        <div v-if="!isPro" class="text-caption text-muted">
                            Mit Plus plant NoRain die nächsten Tage um Regen und Gegenwind herum, vergleicht bis zu
                            drei Varianten pro Tag und schlägt die beste Abfahrtszeit vor.
                        </div>
                        <q-select
                            v-else
                            v-model="departureWindow"
                            :options="windowOptions"
                            emit-value
                            map-options
                            label="Abfahrt verschieben"
                            outlined
                            dense
                            class="q-mt-sm"
                            style="max-width: 260px"
                        />
                    </div>
                </q-expansion-item>
            </q-card-section>

            <q-card-actions align="right">
                <q-btn flat label="Abbrechen" no-caps @click="onClose" />
                <q-btn
                    color="primary"
                    :label="journey ? 'Speichern und neu planen' : 'Reise planen'"
                    :disable="!isValid"
                    no-caps
                    @click="onSave"
                />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
