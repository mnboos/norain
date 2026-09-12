<script setup lang="ts">
import { computed, defineAsyncComponent } from "vue";
import { useForecastFigures } from "@/queries/forecastParts";

// Loaded lazily so Plotly ends up in its own chunk, fetched only once a forecast is shown.
defineEmits<{ selectSample: [index: number] }>();

const NiceChart = defineAsyncComponent(() => import("./chart/NiceChart.vue"));

const props = defineProps<{ jobId: string; version: string }>();

// The chart data is not part of the job result either: only pages that draw charts fetch it.
const { data, isPending, isError } = useForecastFigures(() => props.jobId, () => props.version);

// Opaque JSON dicts in the API client; NiceChart treats both `data` and `layout` as optional.
const figures = computed(() => data.value ?? []);

/** The backend draws temperature, precipitation and wind; hold their places while loading. */
const PLACEHOLDER_TILES = 3;
</script>

<template>
    <div class="col-12 text-caption">
        Schattierung: 10.–90. Perzentil · Linie: Median · Gepunktet: Einzelprognose. Punkt auswählen für Details.
    </div>
    <!-- Fragment root: the columns drop straight into the caller's `row` grid. -->
    <template v-if="isPending">
        <div v-for="i in PLACEHOLDER_TILES" :key="i" class="col-12 col-sm-6 col-md-3">
            <q-card flat bordered class="square-tile flex flex-center">
                <q-spinner-dots size="2rem" />
            </q-card>
        </div>
    </template>
    <div v-else-if="isError" class="col-12">
        <q-banner dense class="bg-tint-error rounded-borders">Diagramme konnten nicht geladen werden.</q-banner>
    </div>
    <template v-else>
        <div v-for="(fig, i) in figures" :key="i" class="col-12 col-sm-6 col-md-3">
            <q-card flat bordered class="square-tile">
                <NiceChart :figure="fig" @select-sample="$emit('selectSample', $event)" />
            </q-card>
        </div>
    </template>
</template>
