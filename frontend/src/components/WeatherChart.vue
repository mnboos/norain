<script setup lang="ts">
import { computed, defineAsyncComponent, defineComponent, h, toRefs } from "vue";
import { QSkeleton } from "quasar";
import { useI18n } from "vue-i18n";
import { forecastChart, type ChartKind, type ChartSample } from "@/utils/forecastCharts";

defineEmits<{ selectMinutes: [minutes: number] }>();

/** Holds a chart's place while the Plotly chunk downloads. Takes only the tile's class, not the chart props. */
const ChartSkeleton = defineComponent({
    inheritAttrs: false,
    setup(_, { attrs }) {
        const { t } = useI18n();
        return () =>
            h(
                "div",
                { class: attrs.class },
                h(QSkeleton, { square: true, height: "100%", "aria-label": t("charts.loading") }),
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
    /** The selected route position as ride time (min). */
    cursorMinutes?: number;
    samples: ChartSample[];
    /** The forecast's routing profile, which words the axes. */
    profile?: string | null;
}>();

const { kind, version, cursorMinutes, samples, profile } = toRefs(props);
const { t, locale } = useI18n();

// forecastChart words its traces and axes with t(), so a language switch redraws the chart.
const figure = computed(() => forecastChart(kind.value, samples.value, profile.value));
</script>

<template>
    <!-- NiceChart fills its parent, so the parent gives this a height. -->
    <NiceChart
        v-if="figure"
        :key="`${version}:${kind}:${locale}`"
        class="col fit column"
        :figure="figure"
        :cursor-x="cursorMinutes"
        :temperature="kind === 'temperature'"
        @cursor="$emit('selectMinutes', $event)"
    />
    <div v-else class="text-muted">{{ t("charts.noData") }}</div>
</template>
