<script setup lang="ts">
import { ref } from "vue";
import type { PlacesSearchResult } from "@norain/api/models";
import NiceMap from "./NiceMap.vue";
const start = defineModel<PlacesSearchResult | null>("start", { default: null });
const dest = defineModel<PlacesSearchResult | null>("dest", { default: null });
const open = ref(false);
const target = ref("start");
const draftStart = ref<PlacesSearchResult | null>();
const draftDest = ref<PlacesSearchResult | null>();
function show() {
    draftStart.value = start.value;
    draftDest.value = dest.value;
    target.value = start.value ? "dest" : "start";
    open.value = true;
}
function select(point: { lng: number; lat: number }) {
    const place: PlacesSearchResult = {
        type: "Feature",
        geometry: { type: "Point", coordinates: [point.lng, point.lat] },
        properties: {
            name: `${point.lat.toFixed(5)}, ${point.lng.toFixed(5)}`,
            city: null,
            state: "",
            countrycode: "",
            showCanton: false,
        },
    };
    if (target.value === "start") {
        draftStart.value = place;
        target.value = "dest";
    } else draftDest.value = place;
}
function apply() {
    start.value = draftStart.value ?? null;
    dest.value = draftDest.value ?? null;
    open.value = false;
}
</script>
<template>
    <q-btn outline no-caps label="Start und Ziel auf Karte wählen" @click="show" />
    <q-dialog v-model="open">
        <q-card style="width: 800px; max-width: 96vw">
            <q-card-section>
                <div class="text-h6">Route auf der Karte wählen</div>
                <q-btn-toggle
                    v-model="target"
                    class="q-my-sm"
                    no-caps
                    spread
                    :options="[
                        { label: 'Start wählen (blau)', value: 'start' },
                        { label: 'Ziel wählen (rosa)', value: 'dest' },
                    ]"
                />
                <div aria-live="polite">
                    {{ target === "start" ? "Startpunkt" : "Zielpunkt" }} durch Tippen auf die Karte setzen.
                </div>
                <div class="text-caption">Start: {{ draftStart?.properties.name ?? "Noch nicht gewählt" }}</div>
                <div class="text-caption">Ziel: {{ draftDest?.properties.name ?? "Noch nicht gewählt" }}</div>
            </q-card-section>
            <NiceMap
                v-if="open"
                :route-weather="undefined"
                :abfahrtsort="draftStart ?? undefined"
                :zielort="draftDest ?? undefined"
                height="min(50dvh, 480px)"
                pick-location
                @select-location="select"
            />
            <q-card-actions align="right">
                <q-btn v-close-popup flat no-caps label="Abbrechen" />
                <q-btn color="primary" no-caps label="Übernehmen" :disable="!draftStart || !draftDest" @click="apply" />
            </q-card-actions>
        </q-card>
    </q-dialog>
</template>
