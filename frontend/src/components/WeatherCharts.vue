<script setup lang="ts">
import { computed, defineAsyncComponent, defineComponent, h, toRefs } from "vue";
import { QSkeleton } from "quasar";
import { forecastCharts, type ChartSample } from "@/utils/forecastCharts";

defineEmits<{ selectSample: [index: number] }>();

/** Holds a chart's place while the Plotly chunk downloads. Takes only the tile's class, not the chart props. */
const ChartSkeleton = defineComponent({
    inheritAttrs: false,
    setup(_, { attrs }) {
        return () =>
            h(
                "div",
                { class: attrs.class },
                h(QSkeleton, { square: true, height: "100%", "aria-label": "Diagramm wird geladen" }),
            );
    },
});

// Loaded lazily so Plotly ends up in its own chunk, fetched only once a forecast is shown.
const NiceChart = defineAsyncComponent({
    loader: () => import("@/components/chart/NiceChart.vue"),
    loadingComponent: ChartSkeleton,
    delay: 0,
});

const props = defineProps<{
    version: string;
    selectedSample: number;
    samples: ChartSample[];
}>();

const { version, selectedSample, samples } = toRefs(props);

// Drawn from the samples the forecast already carries: the charts need no request of their own.
const figures = computed(() => forecastCharts(samples.value));
</script>

<template>
    <q-card class="fit">
        <q-card-section v-if="figures.length" class="no-padding chart-grid">
            <NiceChart
                v-for="(fig, i) in figures"
                :key="`${version}:${i}`"
                class="chart-tile"
                :figure="fig"
                :selected-sample="selectedSample"
                :samples="samples"
                :directional-wind="i === 2"
                :temperature="i === 0"
                @select-sample="$emit('selectSample', $event)"
            />
        </q-card-section>
        <q-card-section v-else class="no-padding">
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
