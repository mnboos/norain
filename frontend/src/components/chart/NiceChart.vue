<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, toRaw, toRefs, useTemplateRef, watch } from "vue";
import Plotly from "./plotly";
import type { Config, Data, Layout, PlotMouseEvent } from "plotly.js";
import { useQuasar } from "quasar";

// Plotly draws SVG text from layout.font and ignores CSS; keep in sync with --app-font in base.css.
const FONT_FAMILY = '"Lexend Variable", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

const $q = useQuasar();
const emit = defineEmits<{ selectSample: [index: number] }>();
function selectPoint(event: PlotMouseEvent) {
    const custom = event.points[0]?.customdata;
    if (Array.isArray(custom) && typeof custom[0] === "number") emit("selectSample", custom[0]);
}

const props = defineProps<{
    figure: { data?: Data[]; layout?: Partial<Layout> };
}>();

const { figure } = toRefs(props);

const chart = useTemplateRef<Plotly.PlotlyHTMLElement | null>("chartRef");
let resizeObserver: ResizeObserver | null = null;

function buildLayout(): Partial<Layout> {
    const incoming = structuredClone(toRaw(figure.value.layout ?? {}));
    // The card decides the size, so any fixed height/width from the backend
    // would fight the container - drop it and let Plotly autosize.
    delete incoming.height;
    delete incoming.width;
    // The backend sets template="plotly_white" for a sane default when charts are viewed
    // outside this app; here we own theming instead, so every chrome color (axes, grid,
    // fonts - never the per-series data colors, those stay the backend's) follows the
    // app's light/dark state.
    const dark = $q.dark.isActive;
    const ink = dark ? "#e8eef2" : "#1b2733";
    // Decluttered: no gridlines or axis lines - the tick labels carry the scale. Only the zero
    // line stays, as a faint baseline, since it matters for head- vs. tailwind. Charts that cannot
    // cross zero switch it off in the backend, where it would just redraw the plot's bottom border.
    const baseline = dark ? "rgba(232, 238, 242, 0.25)" : "rgba(27, 39, 51, 0.2)";
    const axisTheme = { color: ink, showgrid: false, showline: false, zerolinecolor: baseline, zerolinewidth: 1 };
    return {
        ...incoming,
        autosize: true,
        plot_bgcolor: "transparent",
        paper_bgcolor: "transparent",
        font: { family: FONT_FAMILY, color: ink },
        legend: { ...incoming.legend, font: { ...incoming.legend?.font, family: FONT_FAMILY, color: ink } },
        xaxis: { ...incoming.xaxis, ...axisTheme },
        yaxis: { ...incoming.yaxis, ...axisTheme },
        ...(incoming.yaxis2 ? { yaxis2: { ...incoming.yaxis2, ...axisTheme } } : {}),
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
    // Plotly measures label text during layout; measuring the fallback font before Lexend
    // has loaded leaves ticks and legend entries mis-sized.
    // (jsdom has no document.fonts)
    await document.fonts?.ready;
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
    chart.value?.on("plotly_hover", selectPoint);
    chart.value?.on("plotly_click", selectPoint);
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

// Re-theme (but don't re-fetch data) when the user flips light/dark without a new figure.
watch(
    () => $q.dark.isActive,
    async () => {
        layout.value = buildLayout();
        await render();
    },
);

onBeforeUnmount(() => {
    resizeObserver?.disconnect();
    if (chart.value) {
        chart.value.removeAllListeners("plotly_hover");
        chart.value.removeAllListeners("plotly_click");
        Plotly.purge(chart.value);
    }
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
