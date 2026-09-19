<script setup lang="ts">
import { computed, defineAsyncComponent, defineComponent, h, toRefs } from "vue";
import { QSkeleton } from "quasar";
import { forecastChart, type ChartKind, type ChartSample } from "@/utils/forecastCharts";

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

/** One forecast chart, drawn from the samples the forecast already carries: no request of its own. */
const props = defineProps<{
    kind: ChartKind;
    version: string;
    selectedSample: number;
    samples: ChartSample[];
}>();

const { kind, version, selectedSample, samples } = toRefs(props);

const figure = computed(() => forecastChart(kind.value, samples.value));
</script>

<template>
    <!-- NiceChart fills its parent, so the parent gives this a height. -->
    <NiceChart
        v-if="figure"
        :key="`${version}:${kind}`"
        class="fit"
        :figure="figure"
        :selected-sample="selectedSample"
        :samples="samples"
        :temperature="kind === 'temperature'"
        @select-sample="$emit('selectSample', $event)"
    />
    <div v-else class="text-muted">Keine Diagrammdaten verfügbar.</div>
</template>
