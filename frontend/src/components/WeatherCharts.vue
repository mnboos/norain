<script setup lang="ts">
import { computed, defineAsyncComponent, toRefs } from "vue";
import { useForecastFigures } from "@/queries/forecastParts";
import type { TimedSample } from "@/utils/forecastSelection";

defineEmits<{ selectSample: [index: number] }>();

// Loaded lazily so Plotly ends up in its own chunk, fetched only once a forecast is shown.
const NiceChart = defineAsyncComponent(() => import("@/components/chart/NiceChart.vue"));

const props = defineProps<{
    jobId: string;
    version: string;
    selectedSample: number;
    samples: TimedSample[];
}>();

const { jobId, version, selectedSample, samples } = toRefs(props);

// The chart data is not part of the job result either: only pages that draw charts fetch it.
const { data, isPending, isError } = useForecastFigures(jobId, version);

// Opaque JSON dicts in the API client; NiceChart treats both `data` and `layout` as optional.
const figures = computed(() => data.value ?? []);

/** The backend draws temperature, precipitation and wind; hold their places while loading. */
const PLACEHOLDER_TILES = 3;
</script>

<template>
    <q-card class="fit">
        <q-card-section v-if="isPending" class="no-padding chart-grid">
            <div v-for="i in PLACEHOLDER_TILES" :key="i" class="chart-tile">
                <q-skeleton square height="100%" aria-label="Diagramm wird geladen" />
            </div>
        </q-card-section>
        <q-card-section v-else-if="isError" class="no-padding">
            <q-banner dense class="bg-tint-error rounded-borders">Diagramme konnten nicht geladen werden.</q-banner>
        </q-card-section>
        <q-card-section v-else-if="figures.length" class="no-padding chart-grid">
            <NiceChart
                v-for="(fig, i) in figures"
                :key="`${jobId}:${version}:${i}`"
                class="chart-tile"
                :figure="fig"
                :selected-sample="selectedSample"
                :samples="samples"
                :directional-wind="i === 2"
                :temperature="i === 0"
                @select-sample="$emit('selectSample', $event)"
            />
        </q-card-section>
        <q-card-section v-if="!isPending && !isError && !figures.length" class="no-padding">
            <q-banner dense>Keine Diagrammdaten verfügbar.</q-banner>
        </q-card-section>
    </q-card>
</template>

<style scoped>
.chart-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 4px;
    width: 100vw;
    height: 100%;
}
.chart-tile {
    min-width: 0;
    min-height: 0;
}
</style>
