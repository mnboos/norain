<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, toRaw, toRefs, useTemplateRef, watch } from "vue";
import Plotly from "./plotly";
import type { Config, Data, Layout } from "plotly.js";
import { useQuasar } from "quasar";

const $q = useQuasar();

const props = defineProps<{
    figure: { data?: Data[]; layout?: Partial<Layout> };
}>();

const { figure } = toRefs(props);

const chart = useTemplateRef<Plotly.PlotlyHTMLElement | null>("chartRef");
let resizeObserver: ResizeObserver | null = null;

function buildLayout(): Partial<Layout> {
    const incoming = structuredClone(toRaw(figure.value.layout ?? {}));
    // The card decides the size (square), so any fixed height/width from the backend
    // would fight the container - drop it and let Plotly autosize.
    delete incoming.height;
    delete incoming.width;
    return {
        ...incoming,
        autosize: true,
        plot_bgcolor: "transparent",
        paper_bgcolor: "transparent",
        legend: { ...incoming.legend, font: { color: $q.dark.isActive ? "white" : "black" } },
        grid: { rows: 1, columns: 1, pattern: "independent" },
    };
}

const data = ref<Data[]>(structuredClone(toRaw(figure.value.data ?? [])));
const layout = ref<Partial<Layout>>(buildLayout());
const config = ref<Partial<Config>>({
    responsive: true,
    autosizable: true,
    displaylogo: false,
    displayModeBar: false,
});

async function render() {
    const el = chart.value;
    if (el) {
        await Plotly.react(el, data.value, layout.value, config.value);
        Plotly.Plots.resize(el);
    } else {
        console.error("Chart element not found");
    }
}

onMounted(async () => {
    if (chart.value) {
        resizeObserver = new ResizeObserver(() => {
            if (chart.value) Plotly.Plots.resize(chart.value);
        });
        resizeObserver.observe(chart.value);
    }
    await render();
});

watch(
    figure,
    async () => {
        data.value = structuredClone(toRaw(figure.value.data ?? []));
        layout.value = buildLayout();
        await render();
    },
    { deep: true },
);

onBeforeUnmount(() => {
    resizeObserver?.disconnect();
    if (chart.value) Plotly.purge(chart.value);
});
</script>

<template>
    <div ref="chartRef" class="chart-container" />
</template>

<style scoped>
.chart-container {
    width: 100%;
    height: 100%;
}
</style>
