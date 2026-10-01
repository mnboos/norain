<script setup lang="ts">
import { computed, ref, watch, type Ref } from "vue";
import { QSelect } from "quasar";
import { useI18n } from "vue-i18n";
import { tp } from "@/i18n";
import {
    JourneyInKindEnum,
    RoadPrefsInClimbingEnum as Climbing,
    RoadPrefsInFerriesEnum as Ferries,
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
import { headingOptions, paceHint } from "@/utils/randomRides";
import { BIKE_PROFILE_OPTIONS, followProfileDefaults, hasWindEffort } from "@/utils/bikeProfiles";

const props = defineProps<{
    modelValue: boolean;
    /** Edit this random ride instead of creating one. */
    ride?: JourneyOut;
}>();

const emit = defineEmits<{
    "update:modelValue": [value: boolean];
    save: [data: JourneyIn];
}>();

const { t } = useI18n();
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
followProfileDefaults(profile, [[kilometres, 40]]);
const heading = ref<number | null>(null);
const poiCategories = ref<string[]>([]);
const surface = ref(Surface.Any);
const climbing = ref(Climbing.Neutral);
const traffic = ref(Traffic.Neutral);
const towns = ref(Towns.Neutral);
const ferries = ref(Ferries.Neutral);
// Every tier: three variants to pick from and save as routes. Plus: weigh them by the weather.
const considerWeather = ref(false);
// Off until the rider chooses it; see WeatherRoutingChoice.
const avoidRain = ref(false);
const avoidHeadwind = ref(false);
const avoidShade = ref(false);

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
    ferries.value = ride.roadPrefs.ferries ?? Ferries.Neutral;
    avoidRain.value = ride.weatherPrefs.avoidRain ?? false;
    avoidHeadwind.value = ride.weatherPrefs.avoidHeadwind ?? false;
    avoidShade.value = ride.weatherPrefs.avoidShade ?? false;
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

// Built in computeds, so every label follows a language switch.
const lengthOptions = computed(() => [
    { label: t("randomForm.length.time"), value: "time" },
    { label: t("randomForm.length.distance"), value: "distance" },
]);
const shapeOptions = computed(() => [
    { label: t("randomForm.shape.loop"), value: true },
    { label: t("randomForm.shape.toDestination"), value: false },
]);
const surfaceOptions = computed(() => [
    { label: t("roadPrefs.any"), value: Surface.Any },
    { label: t("roadPrefs.surface.avoidUnpaved"), value: Surface.AvoidUnpaved },
    { label: t("roadPrefs.surface.pavedOnly"), value: Surface.PavedOnly },
]);
// The terrain is the first thing to know about a ride out; it sits beside the profile.
const terrainOptions = computed(() => [
    { label: t("roadPrefs.climbing.flat"), value: Climbing.Avoid },
    { label: t("roadPrefs.any"), value: Climbing.Neutral },
    { label: t("roadPrefs.climbing.hilly"), value: Climbing.Hilly },
]);
const trafficOptions = computed(() => [
    { label: t("roadPrefs.any"), value: Traffic.Neutral },
    { label: t("roadPrefs.traffic.avoidMain"), value: Traffic.AvoidMain },
    { label: tp(profile.value, "roadPrefs.traffic.preferNetwork"), value: Traffic.AvoidOffNetwork },
]);
const townOptions = computed(() => [
    { label: t("roadPrefs.any"), value: Towns.Neutral },
    { label: t("roadPrefs.towns.avoid"), value: Towns.Avoid },
]);
const ferryOptions = computed(() => [
    { label: t("roadPrefs.any"), value: Ferries.Neutral },
    { label: t("roadPrefs.ferries.avoid"), value: Ferries.Avoid },
]);
const headings = computed(() => headingOptions());

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
            (roundTrip.value
                ? t("randomForm.defaultNameLoop", { start: start.value.properties.name })
                : t("randomForm.defaultNameTo", { dest: target.properties.name })),
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
        roadPrefs: {
            surface: surface.value,
            climbing: climbing.value,
            traffic: traffic.value,
            towns: towns.value,
            ferries: ferries.value,
        },
        randomPrefs: {
            roundTrip: roundTrip.value,
            heading: heading.value,
            considerWeather: weatherMode.value,
        },
        weatherPrefs: {
            avoidRain: weatherMode.value && avoidRain.value,
            avoidHeadwind: weatherMode.value && avoidHeadwind.value && hasWindEffort(profile.value),
            avoidShade: weatherMode.value && avoidShade.value,
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
                <q-item-label overline>{{ ride ? t("randomForm.editTitle") : t("randomForm.newTitle") }}</q-item-label>
                <div class="text-caption text-muted">{{ tp(profile, "randomForm.intro") }}</div>
            </q-card-section>

            <q-card-section class="q-gutter-md">
                <q-btn-toggle
                    v-model="roundTrip"
                    :options="shapeOptions"
                    toggle-color="primary"
                    spread
                    no-caps
                    :aria-label="t('randomForm.shape.label')"
                />

                <q-select
                    v-model="start"
                    :label="t('routeForm.start')"
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
                    :label="t('routeForm.dest')"
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
                    <div class="text-caption q-mb-sm">{{ t("randomForm.profile") }}</div>
                    <q-btn-toggle
                        v-model="profile"
                        :options="BIKE_PROFILE_OPTIONS"
                        toggle-color="primary"
                        spread
                        no-caps
                        size="sm"
                    />
                </div>

                <div>
                    <div class="text-caption q-mb-sm">{{ t("roadPrefs.climbing.label") }}</div>
                    <q-btn-toggle
                        v-model="climbing"
                        :options="terrainOptions"
                        toggle-color="primary"
                        spread
                        no-caps
                        size="sm"
                        :aria-label="t('roadPrefs.climbing.label')"
                    />
                    <div class="text-caption text-muted q-mt-xs">
                        <template v-if="climbing === Climbing.Avoid">{{ t("roadPrefs.climbing.flatHint") }}</template>
                        <template v-else-if="climbing === Climbing.Hilly">
                            {{ t("roadPrefs.climbing.hillyHint") }}
                        </template>
                        <template v-else>{{ t("roadPrefs.climbing.anyHint") }}</template>
                    </div>
                </div>

                <div>
                    <div class="row items-center justify-between">
                        <div class="text-caption">{{ t("randomForm.length.label") }}</div>
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
                            :aria-label="tp(profile, 'randomForm.length.hoursLabel')"
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
                            :aria-label="t('randomForm.length.kmLabel')"
                        />
                        <div class="text-body2 text-weight-medium" style="min-width: 4rem; text-align: right">
                            {{ length === "time" ? `${hours} h` : `${kilometres} km` }}
                        </div>
                    </div>
                    <div class="text-caption text-muted">{{ hint }}</div>
                </div>

                <q-select
                    v-model="heading"
                    :options="headings"
                    emit-value
                    map-options
                    :label="roundTrip ? t('randomForm.headingLoop') : t('randomForm.headingDetour')"
                    outlined
                    dense
                    style="max-width: 260px"
                />

                <div class="row q-col-gutter-sm">
                    <q-input v-model="date" class="col-6" type="date" :label="t('routeForm.date')" outlined dense />
                    <q-input
                        v-model="departure"
                        class="col-6"
                        :label="tp(profile, 'routeForm.departure')"
                        outlined
                        dense
                        mask="##:##"
                        fill-mask
                        :error="!TIME.test(departure)"
                        :error-message="t('routeForm.timeFormat')"
                        no-error-icon
                    />
                </div>

                <q-input v-model="name" :label="t('routeForm.nameOptional')" outlined dense no-error-icon />

                <ChipMultiSelect
                    v-model="poiCategories"
                    :label="t('randomForm.pois')"
                    :hint="t('randomForm.poisHint')"
                    :options="POI_CATEGORIES.filter(c => c.value !== 'lodging')"
                />

                <div data-testid="random-mode">
                    <div class="row items-center q-gutter-x-sm">
                        <span class="text-caption">{{ t("randomForm.weather") }}</span>
                        <q-badge v-if="!weatherRouting" color="accent" label="Plus" />
                    </div>
                    <q-toggle
                        :model-value="weatherMode"
                        :disable="!weatherRouting"
                        :label="t('randomForm.considerWeather')"
                        @update:model-value="considerWeather = $event"
                    />
                    <div class="text-caption text-muted">
                        <template v-if="weatherMode">{{ t("randomForm.weatherOn") }}</template>
                        <template v-else>{{ t("randomForm.weatherOff") }}</template>
                    </div>
                    <WeatherRoutingChoice
                        v-if="weatherMode"
                        v-model:avoid-rain="avoidRain"
                        v-model:avoid-headwind="avoidHeadwind"
                        v-model:avoid-shade="avoidShade"
                        :headwind="hasWindEffort(profile)"
                        :profile="profile"
                        class="q-mt-sm"
                    />
                </div>

                <q-expansion-item dense :label="t('roadPrefs.title')" header-class="text-caption q-px-none">
                    <div class="row q-col-gutter-sm q-pt-sm">
                        <q-select
                            v-model="surface"
                            class="col-12 col-sm-6"
                            :options="surfaceOptions"
                            emit-value
                            map-options
                            :label="t('roadPrefs.surface.label')"
                            outlined
                            dense
                        />
                        <q-select
                            v-model="traffic"
                            class="col-12 col-sm-6"
                            :options="trafficOptions"
                            emit-value
                            map-options
                            :label="t('roadPrefs.traffic.label')"
                            outlined
                            dense
                        />
                        <q-select
                            v-model="towns"
                            class="col-12 col-sm-6"
                            :options="townOptions"
                            emit-value
                            map-options
                            :label="t('roadPrefs.towns.label')"
                            outlined
                            dense
                        />
                        <q-select
                            v-model="ferries"
                            class="col-12 col-sm-6"
                            :options="ferryOptions"
                            emit-value
                            map-options
                            :label="t('roadPrefs.ferries.label')"
                            outlined
                            dense
                        />
                    </div>
                </q-expansion-item>
            </q-card-section>

            <q-card-actions align="right">
                <q-btn flat :label="t('common.cancel')" no-caps @click="onClose" />
                <q-btn
                    color="primary"
                    :label="ride ? t('randomForm.saveAndReplan') : t('randomForm.roll')"
                    :disable="!isValid"
                    no-caps
                    @click="onSave"
                />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
