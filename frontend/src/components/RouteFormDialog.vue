<script setup lang="ts">
import { computed, ref, type Ref } from "vue";
import { QSelect } from "quasar";
import {
    symSharpElectricBike,
    symSharpElectricMoped,
    symSharpPedalBike,
    symSharpSchedule,
} from "@quasar/extras/material-symbols-sharp";
import RouteLocationPicker from "@/components/RouteLocationPicker.vue";
import RouteEditorDialog from "@/components/RouteEditorDialog.vue";
import DepartureFlexibility from "@/components/DepartureFlexibility.vue";
import PlaceSearchItem from "@/components/PlaceSearchItem.vue";
import { placeLabel } from "@/utils/placeLabel";
import type { PlacesSearchResult, RecurringRouteIn } from "@norain/api/models";
import { usePlaceSearch } from "@/queries/places";
import type { LonLat } from "@/utils/routeEditing";

defineProps<{
    modelValue: boolean;
}>();

const emit = defineEmits<{
    "update:modelValue": [value: boolean];
    save: [data: RecurringRouteIn];
}>();

const name = ref("");
const description = ref("");
const start = ref<PlacesSearchResult | null>(null);
const dest = ref<PlacesSearchResult | null>(null);
const profile = ref("bike");
// Kept when start or destination changes: the user resets them in the editor if they no longer fit.
const viaPoints = ref<LonLat[]>([]);
const editing = ref(false);

function lonLat(place: PlacesSearchResult | null): LonLat | null {
    const [lon, lat] = place?.geometry.coordinates ?? [];
    return lon === undefined || lat === undefined ? null : [lon, lat];
}
const startLonLat = computed(() => lonLat(start.value));
const destLonLat = computed(() => lonLat(dest.value));
const days = ref<number[]>([1, 2, 3, 4, 5]);
const time = ref("08:00");
const twoWay = ref(false);
const returnTime = ref("17:00");
const returnDays = ref<number[]>([1, 2, 3, 4, 5]);
const returnValid = computed(() => /^([01]\d|2[0-3]):[0-5]\d$/.test(returnTime.value) && returnDays.value.length > 0);
const flexBefore = ref(0);
const flexAfter = ref(0);

const filterStart = ref("");
const filterDest = ref("");

const defaultSearchLocation = { zoom: 12, lat: 47.5, lon: 9.3 };
const { data: placesStart } = usePlaceSearch(filterStart, defaultSearchLocation);
const { data: placesDest } = usePlaceSearch(filterDest, defaultSearchLocation);

// Quasar QSelect requires doneFn() to signal async filtering is complete.
// Copied from the working map.vue pattern.
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

// Select the chosen place's name on focus, so typing replaces it instead of appending to it.
function selectInputText(e: Event) {
    if (e.target instanceof HTMLInputElement) e.target.select();
}

function toggleDay(day: number) {
    const idx = days.value.indexOf(day);
    if (idx >= 0) {
        days.value = days.value.filter(d => d !== day);
    } else {
        days.value = [...days.value, day].sort();
    }
}

const dayLabels = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

// The input uses fill-mask, so a half-typed time is still five characters ("17:__").
// Only a complete HH:MM counts; anything else would reach the cron string as NaN.
const parsedTime = computed(() => {
    const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(time.value);
    return match ? { h: Number(match[1]), m: Number(match[2]) } : null;
});

const scheduleDescription = computed(() => {
    if (!days.value.length || !parsedTime.value) return "";
    const dayNames = days.value.map(d => dayLabels[d - 1] ?? "");
    const { h, m } = parsedTime.value;
    return `${dayNames.join(", ")} um ${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
});

const scheduleCron = computed(() => {
    if (!days.value.length || !parsedTime.value) return "";
    const { h, m } = parsedTime.value;
    return `${m} ${h} * * ${days.value.join(",")}`;
});

const profileOptions = [
    { label: "Velo", value: "bike", icon: symSharpPedalBike },
    { label: "E-Bike", value: "ebike", icon: symSharpElectricBike },
    { label: "S-Pedelec", value: "fast_ebike", icon: symSharpElectricMoped },
];

const isValid = computed(
    () => !!name.value.length && !!start.value && !!dest.value && !!days.value.length && !!parsedTime.value && (!twoWay.value || returnValid.value),
);

function onSave() {
    if (!start.value || !dest.value) return;
    emit("save", {
        name: name.value,
        description: description.value,
        startLat: start.value.geometry.coordinates[1] ?? 0,
        startLon: start.value.geometry.coordinates[0] ?? 0,
        startName: start.value.properties.name,
        destLat: dest.value.geometry.coordinates[1] ?? 0,
        destLon: dest.value.geometry.coordinates[0] ?? 0,
        destName: dest.value.properties.name,
        viaPoints: viaPoints.value,
        profile: profile.value,
        scheduleCron: scheduleCron.value,
        departureFlexBeforeMinutes: flexBefore.value,
        departureFlexAfterMinutes: flexAfter.value,
        scheduleDescription: scheduleDescription.value,
        returnScheduleCron: twoWay.value ? `${Number(returnTime.value.slice(3))} ${Number(returnTime.value.slice(0, 2))} * * ${returnDays.value.join(",")}` : null,
        returnScheduleDescription: twoWay.value ? `${returnDays.value.map(d => dayLabels[d - 1]).join(", ")} um ${returnTime.value}` : "",
    });
    emit("update:modelValue", false);
}

function onClose() {
    emit("update:modelValue", false);
}
</script>

<template>
    <q-dialog :model-value="modelValue" persistent :maximized="$q.screen.xs" class="row" @update:model-value="onClose">
        <q-card class="col-3" style="min-width: 50%">
            <q-card-section>
                <q-item-label overline>Neue Route</q-item-label>
            </q-card-section>

            <q-card-section class="q-gutter-md">
                <q-input
                    v-model="name"
                    label="Name"
                    outlined
                    no-error-icon
                    dense
                    autofocus
                    :rules="[(val: string) => !!val || 'Pflichtfeld']"
                />

                <q-input v-model="description" label="Beschreibung (optional)" outlined dense type="textarea" />

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

                <div v-if="startLonLat && destLonLat" class="row items-center q-gutter-sm">
                    <q-btn outline no-caps label="Strecke anpassen" @click="editing = true" />
                    <span v-if="viaPoints.length" class="text-caption">
                        {{ viaPoints.length }} Zwischenpunkt{{ viaPoints.length === 1 ? "" : "e" }}
                    </span>
                    <RouteEditorDialog
                        v-model="editing"
                        :start="startLonLat"
                        :dest="destLonLat"
                        :profile="profile"
                        :via-points="viaPoints"
                        @apply="viaPoints = $event"
                    />
                </div>

                <div>
                    <div class="text-caption q-mb-sm">Tage</div>
                    <q-btn-group stretch class="full-width" flat>
                        <q-btn
                            v-for="(label, i) in dayLabels"
                            :key="i"
                            class="full-width"
                            :label="label"
                            :outline="!days.includes(i + 1)"
                            :color="days.includes(i + 1) ? 'primary' : 'grey'"
                            size="sm"
                            dense
                            @click="toggleDay(i + 1)"
                        />
                    </q-btn-group>
                </div>

                <q-input
                    v-model="time"
                    label="Abfahrtszeit"
                    outlined
                    dense
                    mask="##:##"
                    fill-mask
                    no-error-icon
                    :rules="[() => !!parsedTime || 'Uhrzeit als HH:MM']"
                >
                    <template #append>
                        <q-icon :name="symSharpSchedule" class="cursor-pointer">
                            <q-popup-proxy cover transition-show="scale" transition-hide="scale">
                                <q-time v-model="time" />
                            </q-popup-proxy>
                        </q-icon>
                    </template>
                </q-input>

                <q-toggle v-model="twoWay" label="Hin- und Rückfahrt als eine Route speichern" />
                <div v-if="twoWay" class="q-gutter-sm">
                    <q-input
v-model="returnTime" label="Abfahrtszeit der Rückfahrt" outlined dense mask="##:##" fill-mask
                        :rules="[() => returnValid || 'Gültige Uhrzeit und mindestens einen Tag auswählen']" />
                    <div class="text-caption">Tage der Rückfahrt</div>
                    <q-option-group
v-model="returnDays" type="checkbox" inline
                        :options="dayLabels.map((label, i) => ({ label, value: i + 1 }))" />
                    <p class="text-caption">Die Rückfahrt wird vom Ziel zum Start separat berechnet. Beide Fahrten zählen zusammen als eine Route.</p>
                </div>

                <DepartureFlexibility v-model:before="flexBefore" v-model:after="flexAfter" />

                <div v-if="scheduleDescription" class="text-body2 text-muted">
                    {{ scheduleDescription }}
                </div>

                <div>
                    <div class="text-caption q-mb-sm">Profil</div>
                    <q-btn-toggle v-model="profile" :options="profileOptions" toggle-color="primary" spread size="sm" />
                </div>
            </q-card-section>

            <q-card-actions align="right">
                <q-btn flat label="Abbrechen" no-caps @click="onClose" />
                <q-btn color="primary" label="Speichern" :disable="!isValid" no-caps @click="onSave" />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
