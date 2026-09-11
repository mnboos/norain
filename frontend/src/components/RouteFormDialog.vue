<script setup lang="ts">
import { computed, ref, type Ref } from "vue";
import { QSelect } from "quasar";
import {
    symSharpDirectionsWalk,
    symSharpElectricBike,
    symSharpElectricCar,
    symSharpElectricMoped,
    symSharpPedalBike,
    symSharpSchedule,
} from "@quasar/extras/material-symbols-sharp";
import PlaceSearchItem from "@/components/PlaceSearchItem.vue";
import { DefaultApi, type PlacesSearchResult } from "@norain/api";
import type { RecurringRouteIn } from "@norain/api";
import { useQuery } from "@tanstack/vue-query";

const props = defineProps<{
    modelValue: boolean;
}>();

const emit = defineEmits<{
    "update:modelValue": [value: boolean];
    save: [data: RecurringRouteIn];
}>();

const api = new DefaultApi();

const name = ref("");
const description = ref("");
const start = ref<PlacesSearchResult | null>(null);
const dest = ref<PlacesSearchResult | null>(null);
const profile = ref("bike");
const days = ref<number[]>([1, 2, 3, 4, 5]);
const time = ref("08:00");

const filterStart = ref("");
const filterDest = ref("");

function usePlaceSearch(filter: Ref<string>) {
    return useQuery({
        queryKey: ["placeSearch", filter],
        enabled: () => filter.value.length > 2,
        queryFn: () =>
            api.coreApiSearch({
                query: filter.value,
                zoom: 12,
                lat: 47.5,
                lon: 9.3,
            }),
        initialData: [],
    });
}

const { data: placesStart } = usePlaceSearch(filterStart);
const { data: placesDest } = usePlaceSearch(filterDest);

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

function toggleDay(day: number) {
    const idx = days.value.indexOf(day);
    if (idx >= 0) {
        days.value = days.value.filter(d => d !== day);
    } else {
        days.value = [...days.value, day].sort();
    }
}

const dayLabels = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

const scheduleDescription = computed(() => {
    if (!days.value.length || !time.value) return "";
    const dayNames = days.value.map(d => dayLabels[d - 1]!);
    const [h, m] = time.value.split(":").map(Number);
    return `${dayNames.join(", ")} um ${String(h).padStart(2, "0")}:${String(m ?? 0).padStart(2, "0")}`;
});

const scheduleCron = computed(() => {
    if (!days.value.length || !time.value) return "";
    const [h, m] = time.value.split(":").map(Number);
    return `${m ?? 0} ${h} * * ${days.value.join(",")}`;
});

const profileOptions = [
    { label: "Fuss", value: "foot", icon: symSharpDirectionsWalk },
    { label: "Velo", value: "bike", icon: symSharpPedalBike },
    { label: "E-Bike", value: "ebike", icon: symSharpElectricBike },
    { label: "S-Pedelec", value: "fast_ebike", icon: symSharpElectricMoped },
    { label: "Auto", value: "car", icon: symSharpElectricCar },
];

const isValid = computed(
    () => !!name.value.length && !!start.value && !!dest.value && !!days.value.length && !!time.value.length,
);

function onSave() {
    if (!start.value || !dest.value) return;
    emit("save", {
        name: name.value,
        description: description.value,
        startLat: start.value.geometry.coordinates[1]!,
        startLon: start.value.geometry.coordinates[0]!,
        startName: start.value.properties.name,
        destLat: dest.value.geometry.coordinates[1]!,
        destLon: dest.value.geometry.coordinates[0]!,
        destName: dest.value.properties.name,
        profile: profile.value,
        scheduleCron: scheduleCron.value,
        scheduleDescription: scheduleDescription.value,
    });
    emit("update:modelValue", false);
}

function onClose() {
    emit("update:modelValue", false);
}
</script>

<template>
    <q-dialog :model-value="modelValue" persistent @update:model-value="onClose">
        <q-card style="min-width: 500px; max-width: 600px">
            <q-card-section>
                <div class="text-h6">Neue Route</div>
            </q-card-section>

            <q-card-section class="q-gutter-md">
                <q-input
                    v-model="name"
                    label="Name"
                    outlined
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
                    :input-debounce="100"
                    :options="placesStart ?? []"
                    @filter="onFilterStart"
                >
                    <template #selected-item="scope">
                        <PlaceSearchItem
                            v-if="scope.opt"
                            :feature="scope.opt"
                            :focused="false"
                            inline
                            :clickable="false"
                        />
                    </template>
                    <template #option="scope">
                        <PlaceSearchItem
                            :feature="scope.opt"
                            :focused="scope.focused"
                            :inline="false"
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
                    :input-debounce="100"
                    :options="placesDest ?? []"
                    @filter="onFilterDest"
                >
                    <template #selected-item="scope">
                        <PlaceSearchItem
                            v-if="scope.opt"
                            :feature="scope.opt"
                            :focused="false"
                            inline
                            :clickable="false"
                        />
                    </template>
                    <template #option="scope">
                        <PlaceSearchItem
                            :feature="scope.opt"
                            :focused="scope.focused"
                            :inline="false"
                            clickable
                            @click="scope.toggleOption(scope.opt)"
                        />
                    </template>
                </q-select>

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

                <q-input v-model="time" label="Abfahrtszeit" outlined dense mask="##:##" fill-mask>
                    <template #append>
                        <q-icon :name="symSharpSchedule" class="cursor-pointer">
                            <q-popup-proxy cover transition-show="scale" transition-hide="scale">
                                <q-time v-model="time" />
                            </q-popup-proxy>
                        </q-icon>
                    </template>
                </q-input>

                <div v-if="scheduleDescription" class="text-body2 text-muted">
                    {{ scheduleDescription }}
                </div>

                <div>
                    <div class="text-caption q-mb-sm">Profil</div>
                    <q-btn-toggle v-model="profile" :options="profileOptions" toggle-color="primary" spread size="sm" />
                </div>
            </q-card-section>

            <q-card-actions align="right">
                <q-btn flat label="Abbrechen" @click="onClose" />
                <q-btn color="primary" label="Speichern" :disable="!isValid" @click="onSave" />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
