<script setup lang="ts">
import { computed, defineAsyncComponent, toRefs } from "vue";
import { useForecastFigures } from "@/queries/forecastParts";
import type { TimedSample } from "@/utils/forecastSelection";

defineEmits<{ selectSample: [index: number] }>();

// Loaded lazily so Plotly ends up in its own chunk, fetched only once a forecast is shown.
const NiceChart = defineAsyncComponent(() => import("./chart/NiceChart.vue"));

const props = defineProps<{
    jobId: string;
    version: string;
    selectedSample: number;
    samples: TimedSample[];
    /** Stack the charts in a column (beside a tall map) instead of a row (above a wide one). */
    vertical?: boolean;
}>();

const { jobId, version, selectedSample, samples, vertical } = toRefs(props);

// The chart data is not part of the job result either: only pages that draw charts fetch it.
const { data, isPending, isError } = useForecastFigures(jobId, version);

// Opaque JSON dicts in the API client; NiceChart treats both `data` and `layout` as optional.
const figures = computed(() => data.value ?? []);

/** The backend draws temperature, precipitation and wind; hold their places while loading. */
const PLACEHOLDER_TILES = 3;

/** How big a chart gets: about a quarter of the screen's height (3 of 12 rows), leaving the rest to the map. */
const CHART_SIZE = "25vh";
// Inline, not a scoped class: NiceChart is an async component, and a style attribute reaches its
// root through the wrapper without relying on scoped-CSS inheritance.
// In a row the flex basis is the width, and min-width 0 lets three squares shrink to fit a phone.
// In a column a flex basis would set the height and leave the width to nothing, so the width is
// fixed instead. Either way the square plot div inside NiceChart makes the height follow.
const tileStyle = computed(() =>
    vertical.value ? { flex: "none", width: CHART_SIZE } : { flex: `0 1 ${CHART_SIZE}`, minWidth: 0 },
);
const containerClass = computed(() =>
    vertical.value ? "column no-wrap q-gutter-y-xs" : "row no-wrap items-start justify-center q-gutter-x-xs",
);
</script>

<template>
    <!-- Square tiles, CHART_SIZE each. In a row they shrink when three do not fit (a phone), so the
         row is never taller than CHART_SIZE; in a column they stay CHART_SIZE wide. -->
    <q-card-section v-if="isPending" class="no-padding" :class="containerClass">
        <div v-for="i in PLACEHOLDER_TILES" :key="i" class="chart-tile" :style="tileStyle">
            <q-skeleton square height="100%" aria-label="Diagramm wird geladen" />
        </div>
    </q-card-section>
    <q-card-section v-else-if="isError" class="no-padding">
        <q-banner dense class="bg-tint-error rounded-borders">Diagramme konnten nicht geladen werden.</q-banner>
    </q-card-section>
    <q-card-section v-else class="no-padding" :class="containerClass">
        <NiceChart
            v-for="(fig, i) in figures"
            :key="`${jobId}:${version}:${i}`"
            :figure="fig"
            :style="tileStyle"
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
</template>

<style scoped>
.chart-tile {
    aspect-ratio: 1 / 1;
}
</style>
