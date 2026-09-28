<script setup lang="ts">
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { symSharpMyLocation } from "@quasar/extras/material-symbols-sharp";
import type { PlacesSearchResult } from "@norain/api/models";
import { geolocationAvailable, useCurrentLocation } from "@/composables/useCurrentLocation";

defineProps<{ disable?: boolean }>();
const emit = defineEmits<{ select: [place: PlacesSearchResult] }>();

const $q = useQuasar();
const { t } = useI18n();
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
        :aria-label="t('location.current')"
        @click.stop="onClick"
    >
        <q-tooltip>{{ t("location.current") }}</q-tooltip>
    </q-btn>
</template>
