<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, toRefs, useTemplateRef, watch } from "vue";
import Plotly from "./plotly";
import type { Config, Data, Layout, PlotMouseEvent } from "plotly.js";
import { useQuasar } from "quasar";
import { nearestSampleByTime, selectedSeriesPoint, type TimedSample } from "@/utils/forecastSelection";

// Plotly draws SVG text from layout.font and ignores CSS; keep in sync with --app-font in base.css.
const FONT_FAMILY = '"Lexend Variable", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

const $q = useQuasar();
const tooltip = ref<{ time: string; label: string; value: string } | null>(null);
const tooltipPosition = ref({ left: "12px", top: "12px" });
function showPoint(event: PlotMouseEvent) {
    selectPoint(event);
    const point = event.points[0];
    if (!point || typeof point.y !== "number") return;
    const trace = point.data;
    const original: unknown =
        trace.meta && typeof trace.meta === "object" ? Reflect.get(trace.meta, "tooltipTemplate") : undefined;
    const template = typeof original === "string" ? original : "";
    const unit = /%\{y[^}]*\}\s*([^<]*)/.exec(template)?.[1]?.trim() ?? "";
    const label = (/<extra>(.*?)<\/extra>/.exec(template)?.[1] ?? trace.name ?? "Wetter")
        .replace(/:\s*(Median|Einzelprognose)/g, "")
        .replace(/<[^>]*>/g, "");
    const custom = point.customdata;
    tooltip.value = {
        time: Array.isArray(custom) ? `${custom[1]} Uhr` : `${String(point.x)} min`,
        label,
        value: `${point.y.toLocaleString("de-CH", { maximumFractionDigits: unit === "%" ? 0 : 1 })} ${unit}`,
    };
}
function moveTooltip(event: PointerEvent) {
    if (!(event.currentTarget instanceof HTMLElement)) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    tooltipPosition.value = {
        left: `${Math.max(8, Math.min(event.clientX - bounds.left + 14, bounds.width - 204))}px`,
        top: `${Math.max(8, Math.min(event.clientY - bounds.top - 88, bounds.height - 88))}px`,
    };
}
function hideTooltip() {
    tooltip.value = null;
}
const emit = defineEmits<{ selectSample: [index: number] }>();
function selectPoint(event: PlotMouseEvent) {
    const point = event.points[0];
    const custom = point?.customdata;
    const index = Array.isArray(custom) && Number.isInteger(custom[0])
        ? Number(custom[0])
        : typeof point?.x === "number" ? nearestSampleByTime(props.samples ?? [], point.x) : undefined;
    if (index !== undefined && index >= 0 && index < (props.samples?.length ?? 0) && index !== props.selectedSample) {
        emit("selectSample", index);
    }
}

const props = defineProps<{
    figure: { data?: Data[]; layout?: Partial<Layout> };
    directionalWind?: boolean;
    temperature?: boolean;
    selectedSample?: number;
    samples?: TimedSample[];
}>();

const { figure } = toRefs(props);

const chart = useTemplateRef<Plotly.PlotlyHTMLElement | null>("chartRef");
let resizeObserver: ResizeObserver | null = null;

// Background bands for the temperature chart, at fixed temperatures so a colour means the same
// thing on every route. 14-22 °C is the flat zero-penalty stretch of TEMP_CURVE in the
// backend's core/ride_quality.py - keep them in step. Series keep the backend's single colour: colouring
// the line by temperature only repeated the y-axis, and a chart-relative ramp lied about it.
const COMFORT_BAND: [number, number] = [14, 22];
const FROST_LIMIT = 0;

/** Fixed y-range: autorange would otherwise stretch the axis to fit the band shapes. */
function temperatureRange(): [number, number] {
    const values = (figure.value.data ?? []).flatMap(trace =>
        isScatter(trace) && Array.isArray(trace.y)
            ? trace.y.filter((value): value is number => typeof value === "number" && Number.isFinite(value))
            : [],
    );
    const min = values.length ? Math.min(...values) : 0;
    const max = values.length ? Math.max(...values) : 1;
    const padding = Math.max((max - min) * 0.08, 0.5);
    return [min - padding, max + padding];
}

/** Faint comfort / frost bands behind the lines, labelled, and only where the axis shows them. */
function temperatureBands(ink: string, dark: boolean): Pick<Layout, "shapes" | "annotations"> {
    const [low, high] = temperatureRange();
    const shapes: Partial<Plotly.Shape>[] = [];
    const annotations: Partial<Plotly.Annotation>[] = [];
    const band = (from: number, to: number, fill: string, label: string) => {
        const y0 = Math.max(from, low);
        const y1 = Math.min(to, high);
        if (y1 <= y0) return;
        shapes.push({
            type: "rect", layer: "below", xref: "paper", yref: "y",
            x0: 0, x1: 1, y0, y1, fillcolor: fill, line: { width: 0 },
        });
        annotations.push({
            text: label, xref: "paper", yref: "y", x: 1, y: y1, xanchor: "right", yanchor: "top",
            showarrow: false, font: { size: 10, color: ink }, opacity: 0.6,
        });
    };
    band(COMFORT_BAND[0], COMFORT_BAND[1], dark ? "rgba(26, 158, 143, 0.14)" : "rgba(26, 158, 143, 0.09)", "angenehm");
    band(-Infinity, FROST_LIMIT, dark ? "rgba(47, 127, 216, 0.16)" : "rgba(47, 127, 216, 0.08)", "Frost");
    return { shapes, annotations };
}

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
    const revision: unknown = incoming.uirevision;
    return {
        ...incoming,
        uirevision: typeof revision === "string" || typeof revision === "number" ? revision : "forecast-selection",
        autosize: true,
        plot_bgcolor: "transparent",
        paper_bgcolor: "transparent",
        font: { family: FONT_FAMILY, color: ink },
        // Reserve space above the plot for the legend in the shallow forecast cards.
        // Server figures may position legends below the axes, outside the card's bounds.
        title: { ...incoming.title, font: { ...incoming.title?.font, size: 14 }, y: 0.98, yanchor: "top" },
        margin: { ...incoming.margin, t: 100, b: 48, l: 44, r: 44 },
        legend: {
            ...incoming.legend,
            orientation: "h",
            traceorder: "normal",
            tracegroupgap: 0,
            x: 0,
            xanchor: "left",
            y: 1.03,
            yanchor: "bottom",
            font: { ...incoming.legend?.font, family: FONT_FAMILY, color: ink, size: 10 },
        },
        xaxis: { ...incoming.xaxis, ...axisTheme },
        yaxis: {
            ...incoming.yaxis,
            ...axisTheme,
            ...(props.temperature ? { range: temperatureRange(), autorange: false } : {}),
        },
        ...(incoming.yaxis2 ? { yaxis2: { ...incoming.yaxis2, ...axisTheme } } : {}),
        grid: { rows: 1, columns: 1, pattern: "independent" },
        ...(props.temperature
            ? (() => {
                  const bands = temperatureBands(ink, dark);
                  return {
                      shapes: [...(incoming.shapes ?? []), ...(bands.shapes ?? [])],
                      annotations: [...(incoming.annotations ?? []), ...(bands.annotations ?? [])],
                  };
              })()
            : {}),
        ...(props.directionalWind
            ? {
                  title: { text: "Gegenwind / Rückenwind", font: { size: 14 }, y: 0.98, yanchor: "top" },
                  showlegend: false,
                  annotations: [
                      ...(incoming.annotations ?? []),
                      {
                          text: "+ Gegenwind · − Rückenwind",
                          x: 0.5,
                          y: 1.15,
                          xref: "paper",
                          yref: "paper",
                          showarrow: false,
                          font: { size: 11, color: ink },
                      },
                  ],
              }
            : {}),
    };
}

function isScatter(trace: Data): trace is Partial<Plotly.ScatterData> {
    return !trace.type || trace.type === "scatter";
}
function buildData(): Data[] {
    return structuredClone(toRaw(figure.value.data ?? []))
        .filter(
            trace =>
                !props.directionalWind ||
                (isScatter(trace) &&
                    (trace.legendgroup === "headwind" ||
                        (!trace.legendgroup && (trace.name ?? "").startsWith("Gegen")))),
        )
        .map(trace => {
            if (!isScatter(trace)) return trace;
            const scatter: Partial<Plotly.ScatterData> = trace;
            // Keep gaps and true values; only remove the permanent point markers.
            const values = Array.isArray(scatter.y) ? scatter.y : [];
            const isolated = values.map((value, index) =>
                scatter.hoverinfo !== "skip" &&
                scatter.line?.width !== 0 &&
                value != null &&
                values[index - 1] == null &&
                values[index + 1] == null
                    ? 4
                    : 0,
            );
            return {
                ...scatter,
                ...(props.directionalWind ? { visible: true } : {}),
                mode: isolated.some(Boolean) ? "lines+markers" : "lines",
                marker: { ...scatter.marker, size: isolated },
                name: scatter.name?.replace(/:\s*(Ensemble-Median|Median|Einzelprognose)/g, ""),
                hoverinfo: scatter.hoverinfo === "skip" ? "skip" : "none",
                hovertemplate: undefined,
                meta: { tooltipTemplate: scatter.hovertemplate },
                line: {
                    ...scatter.line,
                    width: scatter.line?.width === 0 ? 0 : 1.2,
                    dash: scatter.line?.dash === "dot" ? "dash" : scatter.line?.dash,
                },
            };
        });
}
const data = ref<Data[]>(buildData());
const layout = ref<Partial<Layout>>(buildLayout());
const config = ref<Partial<Config>>({
    responsive: true,
    autosizable: true,
    displaylogo: false,
    displayModeBar: false,
});

// The selected sample is an SVG overlay, not extra Plotly traces: moving a trace's x/y is a
// calc-level restyle (supplyDefaults, calcdata, a redraw of the whole figure), and with three
// charts following every map hover that stalled the page.
const selectionDots = ref<{ cx: number; cy: number; color: string }[]>([]);
const selectionStroke = computed(() => ($q.dark.isActive ? "#e8eef2" : "#1b2733"));
let selectionFrame: number | undefined;
let ready = false;
let disposed = false;
// A getter, so the checks after an await are not narrowed away by the check before it.
const isDisposed = () => disposed;

/** The laid-out axis fields the overlay needs. Plotly has no public data -> pixel API, so these
 *  come from `_fullLayout`; should an upgrade move them, the dots vanish rather than misplace. */
interface PlotAxis {
    _offset: number;
    _length: number;
    c2p: (value: number) => number;
}
function isPlotAxis(value: unknown): value is PlotAxis {
    return (
        typeof value === "object" &&
        value !== null &&
        typeof Reflect.get(value, "_offset") === "number" &&
        typeof Reflect.get(value, "_length") === "number" &&
        typeof Reflect.get(value, "c2p") === "function"
    );
}
/** A trace's axis id ("x", "y2" or unset) -> its laid-out axis ("xaxis", "yaxis2"). */
function plotAxis(el: HTMLElement, letter: "x" | "y", id: unknown): PlotAxis | undefined {
    const fullLayout: unknown = Reflect.get(el, "_fullLayout");
    if (typeof fullLayout !== "object" || fullLayout === null) return undefined;
    const axis: unknown = Reflect.get(fullLayout, `${letter}axis${typeof id === "string" ? id.slice(1) : ""}`);
    return isPlotAxis(axis) ? axis : undefined;
}
/** Pixel position inside the chart, or undefined outside the plot area. */
function toPixel(axis: PlotAxis, value: number): number | undefined {
    const offset = axis.c2p(value);
    return Number.isFinite(offset) && offset >= 0 && offset <= axis._length ? axis._offset + offset : undefined;
}

function scheduleSelection() {
    if (disposed || selectionFrame !== undefined) return;
    selectionFrame = requestAnimationFrame(() => {
        selectionFrame = undefined;
        updateSelection();
    });
}

function updateSelection() {
    const el = chart.value;
    const index = props.selectedSample ?? -1;
    const sample = props.samples?.[index];
    if (!el || !ready || disposed || !sample) {
        selectionDots.value = [];
        return;
    }
    // Sources are the drawn lines only; bands and invisible helper traces carry no dot.
    selectionDots.value = el.data.flatMap(trace => {
        if (
            !isScatter(trace) ||
            trace.hoverinfo === "skip" ||
            trace.line?.width === 0 ||
            trace.visible === false ||
            trace.visible === "legendonly"
        ) {
            return [];
        }
        const point = selectedSeriesPoint(trace, index, sample.elapsedS / 60);
        const xaxis = plotAxis(el, "x", trace.xaxis);
        const yaxis = plotAxis(el, "y", trace.yaxis);
        if (!point || !xaxis || !yaxis) return [];
        const cx = toPixel(xaxis, point.x);
        const cy = toPixel(yaxis, point.y);
        const color = typeof trace.line?.color === "string" ? trace.line.color : "#2f7fd8";
        return cx === undefined || cy === undefined ? [] : [{ cx, cy, color }];
    });
}

watch([() => props.selectedSample, () => props.samples], scheduleSelection);

async function render() {
    // Plotly measures label text during layout; measuring the fallback font before Lexend
    // has loaded leaves ticks and legend entries mis-sized.
    // (jsdom has no document.fonts)
    const fontDocument: Partial<Document> = document;
    ready = false;
    // The old dots sit at the old figure's pixels; drop them until the new layout exists.
    selectionDots.value = [];
    await fontDocument.fonts?.ready;
    if (disposed) return;
    const el = chart.value;
    if (el) {
        await Plotly.react(el, data.value, layout.value, config.value);
        if (isDisposed()) return;
        ready = true;
        Plotly.Plots.resize(el);
        scheduleSelection();
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
    if (disposed) return;
    chart.value?.on("plotly_hover", showPoint);
    chart.value?.on("plotly_click", showPoint);
    chart.value?.on("plotly_unhover", hideTooltip);
    // After every react, resize, re-theme and legend toggle: the axes (and visibility) may have moved.
    chart.value?.on("plotly_afterplot", scheduleSelection);
});

watch(
    figure,
    async () => {
        hideTooltip();
        data.value = buildData();
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
    disposed = true;
    ready = false;
    if (selectionFrame !== undefined) cancelAnimationFrame(selectionFrame);
    resizeObserver?.disconnect();
    if (chart.value) {
        chart.value.removeAllListeners("plotly_hover");
        chart.value.removeAllListeners("plotly_click");
        chart.value.removeAllListeners("plotly_unhover");
        chart.value.removeAllListeners("plotly_afterplot");
        Plotly.purge(chart.value);
    }
});
</script>

<template>
    <div class="chart-shell" @pointermove="moveTooltip" @pointerleave="hideTooltip" @keydown.esc="hideTooltip">
        <div ref="chartRef" class="chart-container" />
        <svg class="chart-selection" aria-hidden="true">
            <circle
                v-for="(dot, i) in selectionDots"
                :key="i"
                data-testid="chart-selection-point"
                :cx="dot.cx"
                :cy="dot.cy"
                r="5"
                :fill="dot.color"
                :stroke="selectionStroke"
                stroke-width="2"
            />
        </svg>
        <Transition name="chart-tooltip">
            <div
                v-if="tooltip"
                class="chart-tooltip"
                :class="{ 'chart-tooltip--dark': $q.dark.isActive }"
                :style="tooltipPosition"
                role="tooltip"
            >
                <div class="chart-tooltip-time">{{ tooltip.time }}</div>
                <div class="chart-tooltip-label">{{ tooltip.label }}</div>
                <strong>{{ tooltip.value }}</strong>
            </div>
        </Transition>
    </div>
</template>

<style scoped>
.chart-shell {
    position: relative;
    width: 100%;
    height: 100%;
}
.chart-container {
    width: 100%;
    height: 100%;
}
/* Plotly's SVG starts at the container's top-left, so plot pixels are overlay pixels. */
.chart-selection {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    pointer-events: none;
}
.chart-tooltip {
    position: absolute;
    z-index: 2;
    pointer-events: none;
    width: 188px;
    max-width: calc(100% - 16px);
    padding: 10px 14px;
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.97);
    color: #1b2733;
    border: 1px solid rgba(100, 120, 135, 0.18);
    box-shadow: 0 6px 24px rgba(15, 30, 45, 0.14);
    transition:
        left 100ms ease-out,
        top 100ms ease-out;
    font-size: 12px;
    line-height: 1.5;
}
.chart-tooltip--dark {
    background: rgba(30, 42, 53, 0.97);
    color: #e8eef2;
}
.chart-tooltip-time {
    opacity: 0.65;
    font-size: 10px;
}
.chart-tooltip-label {
    overflow-wrap: anywhere;
}
.chart-tooltip strong {
    font-size: 17px;
    font-weight: 500;
}
.chart-tooltip-enter-active,
.chart-tooltip-leave-active {
    transition:
        opacity 140ms ease,
        transform 140ms ease;
}
.chart-tooltip-enter-from,
.chart-tooltip-leave-to {
    opacity: 0;
    transform: translateY(4px);
}
@media (prefers-reduced-motion: reduce) {
    .chart-tooltip,
    .chart-tooltip-enter-active,
    .chart-tooltip-leave-active {
        transition: none;
    }
}
</style>
