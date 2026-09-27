<script setup lang="ts">
import { useQuasar } from "quasar";
import { symSharpMyLocation } from "@quasar/extras/material-symbols-sharp";
import type { PlacesSearchResult } from "@norain/api/models";
import { geolocationAvailable, useCurrentLocation } from "@/composables/useCurrentLocation";

defineProps<{ disable?: boolean }>();
const emit = defineEmits<{ select: [place: PlacesSearchResult] }>();

const $q = useQuasar();
const { locating, locate } = useCurrentLocation();

async function onClick() {
    try {
        emit("select", await locate());
    } catch (error) {
        $q.notify({ type: "negative", message: error instanceof Error ? error.message : String(error) });
    }
}
</script>

<template>
    <!-- .stop: a click in the select's append slot would otherwise open its dropdown. -->
    <q-btn
        v-if="geolocationAvailable"
        flat
        round
        dense
        :icon="symSharpMyLocation"
        :loading="locating"
        :disable="disable"
        aria-label="Aktueller Standort"
        @click.stop="onClick"
    >
        <q-tooltip>Aktueller Standort</q-tooltip>
    </q-btn>
</template>
