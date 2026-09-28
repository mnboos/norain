<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, toRefs, useTemplateRef, watch } from "vue";
import Plotly from "./plotly";
import type { Config, Data, Layout, PlotMouseEvent } from "plotly.js";
import { useQuasar } from "quasar";
import { useI18n } from "vue-i18n";
import { intlLocale } from "@/i18n";
import { seriesPointAt } from "@/utils/forecastSelection";

// Plotly draws SVG text from layout.font and ignores CSS; keep in sync with --app-font in base.css.
const FONT_FAMILY = '"Lexend Variable", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

const $q = useQuasar();
const { t } = useI18n();
const tooltip = ref<{ time: string; label: string; value: string } | null>(null);
const tooltipPosition = ref({ left: "12px", top: "12px" });
function showPoint(event: PlotMouseEvent) {
    const point = event.points[0];
    if (!point || typeof point.y !== "number") return;
    const trace = point.data;
    const original: unknown =
        trace.meta && typeof trace.meta === "object" ? Reflect.get(trace.meta, "tooltipTemplate") : undefined;
    const template = typeof original === "string" ? original : "";
    const unit = /%\{y[^}]*\}\s*([^<]*)/.exec(template)?.[1]?.trim() ?? "";
    const label = (/<extra>(.*?)<\/extra>/.exec(template)?.[1] ?? trace.name ?? t("charts.weather"))
        .replace(/:\s*(Median|Einzelprognose|Prognose|Single run|Forecast)/g, "")
        .replace(/<[^>]*>/g, "");
    const custom = point.customdata;
    tooltip.value = {
        time: Array.isArray(custom)
            ? t("common.clock", { time: String(custom[1]) })
            : `${Number(point.x).toLocaleString(intlLocale(), { maximumFractionDigits: 2 })} ${props.xUnit ?? "min"}`,
        label,
        value: `${point.y.toLocaleString(intlLocale(), { maximumFractionDigits: unit === "%" ? 0 : 1 })} ${unit}`,
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
/** The x value under the pointer, anywhere along the plot: not only where a trace has a point. */
const emit = defineEmits<{ cursor: [x: number] }>();
let cursorFrame: number | undefined;
let pendingCursor: number | undefined;
function emitCursor(x: number) {
    pendingCursor = x;
    if (cursorFrame !== undefined) return;
    cursorFrame = requestAnimationFrame(() => {
        cursorFrame = undefined;
        if (pendingCursor !== undefined && pendingCursor !== props.cursorX) emit("cursor", pendingCursor);
        pendingCursor = undefined;
    });
}
function trackPointer(event: PointerEvent) {
    moveTooltip(event);
    const el = chart.value;
    const xaxis = el && ready ? plotAxis(el, "x", undefined) : undefined;
    const yaxis = el && ready ? plotAxis(el, "y", undefined) : undefined;
    if (!el || !xaxis || !yaxis) return;
    const bounds = el.getBoundingClientRect();
    const px = event.clientX - bounds.left - xaxis._offset;
    const py = event.clientY - bounds.top - yaxis._offset;
    if (px < 0 || px > xaxis._length || py < 0 || py > yaxis._length) return;
    const x = xaxis.p2c(px);
    if (Number.isFinite(x)) emitCursor(x);
}
function clickPoint(event: PlotMouseEvent) {
    showPoint(event);
    const x = event.points[0]?.x;
    if (typeof x === "number") emitCursor(x);
}

const props = defineProps<{
    figure: { data?: Data[]; layout?: Partial<Layout> };
    temperature?: boolean;
    xUnit?: string;
    /** The selected position, in the chart's own x units: marked with a rule and a dot on every line. */
    cursorX?: number;
    /** Only lines of this legend group get a dot at the cursor. All lines without it. */
    cursorGroup?: string;
    /** Keep each trace's own line width instead of the uniform 1.5 (the elevation profile's
     * picked variant is drawn thicker than the others). */
    keepLineWidths?: boolean;
}>();

const { figure } = toRefs(props);

const chart = useTemplateRef<Plotly.PlotlyHTMLElement | null>("chartRef");
let resizeObserver: ResizeObserver | null = null;

// The parent decides the chart's size. Below this the full layout's margins (96 px tall, 88 px
// wide) leave hardly any plot, so the chart switches to compactLayout.
const COMPACT_WIDTH = 260;
const COMPACT_HEIGHT = 220;
const compact = ref(false);
// Below this width the legend no longer fits beside the title and moves under it.
const NARROW_WIDTH = 520;
const narrow = ref(false);
function isNarrow(el: HTMLElement): boolean {
    return el.getBoundingClientRect().width < NARROW_WIDTH;
}
function needsCompact(el: HTMLElement): boolean {
    const { width, height } = el.getBoundingClientRect();
    return width < COMPACT_WIDTH || height < COMPACT_HEIGHT;
}

// Background bands for the temperature chart, at fixed temperatures so a colour means the same
// thing on every route. 14-22 °C is the flat zero-penalty stretch of TEMP_CURVE in the
// backend's core/ride_quality.py - keep them in step. That curve scores the *felt* line (wind
// chill at riding speed); the frost limit is about the road and reads the air temperature. Series keep the backend's single colour: colouring
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
            type: "rect",
            layer: "below",
            xref: "paper",
            yref: "y",
            x0: 0,
            x1: 1,
            y0,
            y1,
            fillcolor: fill,
            line: { width: 0 },
        });
        annotations.push({
            text: label,
            xref: "paper",
            yref: "y",
            x: 1,
            y: y1,
            xanchor: "right",
            yanchor: "top",
            showarrow: false,
            font: { size: 10, color: ink },
            opacity: 0.6,
        });
    };
    band(COMFORT_BAND[0], COMFORT_BAND[1], dark ? "rgba(26, 158, 143, 0.14)" : "rgba(26, 158, 143, 0.09)", t("charts.comfortable"));
    band(-Infinity, FROST_LIMIT, dark ? "rgba(47, 127, 216, 0.16)" : "rgba(47, 127, 216, 0.08)", t("badges.frost"));
    return { shapes, annotations };
}

function buildLayout(): Partial<Layout> {
    const incoming = structuredClone(toRaw(figure.value.layout ?? {}));
    // The card decides the size: drop any fixed height/width and let Plotly autosize.
    delete incoming.height;
    delete incoming.width;
    // Every chrome color (axes, grid, fonts - never the per-series data colors, those come
    // from utils/forecastCharts.ts) follows the app's light/dark state.
    const dark = $q.dark.isActive;
    const ink = dark ? "#e8eef2" : "#1b2733";
    // Faint gridlines, no axis lines. The zero line is a little stronger, since it matters for
    // head- vs. tailwind. Charts that cannot cross zero switch it off, where it would just redraw
    // the plot's bottom border.
    const baseline = dark ? "rgba(232, 238, 242, 0.25)" : "rgba(27, 39, 51, 0.2)";
    const grid = dark ? "rgba(232, 238, 242, 0.08)" : "rgba(27, 39, 51, 0.08)";
    const axisTheme = {
        color: ink,
        showgrid: true,
        gridcolor: grid,
        showline: false,
        zerolinecolor: baseline,
        zerolinewidth: 1,
    };
    const revision: unknown = incoming.uirevision;
    const layout: Partial<Layout> = {
        ...incoming,
        uirevision: typeof revision === "string" || typeof revision === "number" ? revision : "forecast-selection",
        autosize: true,
        plot_bgcolor: "transparent",
        paper_bgcolor: "transparent",
        font: { family: FONT_FAMILY, color: ink },
        // The title sits top-left and the legend top-right, on the same line above the plot; on a
        // narrow chart the legend moves onto a line of its own under the title.
        title: { ...incoming.title, font: { ...incoming.title?.font, size: 14 }, y: 0.98, yanchor: "top" },
        margin: { ...incoming.margin, t: narrow.value ? 96 : 48, b: 48, l: 44, r: 44 },
        legend: {
            ...incoming.legend,
            orientation: "h",
            traceorder: "normal",
            tracegroupgap: 0,
            x: narrow.value ? 0 : 1,
            xanchor: narrow.value ? "left" : "right",
            y: 1.02,
            yanchor: "bottom",
            font: { ...incoming.legend?.font, family: FONT_FAMILY, color: ink, size: 10 },
        },
        // Horizontal gridlines only: the x axis draws none.
        xaxis: { ...incoming.xaxis, ...axisTheme, showgrid: false },
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
    };
    return compact.value ? compactLayout(layout, incoming) : layout;
}

/** A small tile, e.g. three charts in one row on a phone (~110 px each) or a short strip above
 *  the map: the full layout's margins alone would fill it. The tooltip still names each series and its unit, so the legend, the axis titles
 *  and the band labels go. The temperature bands themselves stay. */
function compactLayout(layout: Partial<Layout>, incoming: Partial<Layout>): Partial<Layout> {
    // The figures turn automargin on, which would grow the zero margins back to fit the tick
    // labels. The labels go inside the plot instead.
    const axis = (base: Partial<Plotly.LayoutAxis> | undefined) => ({
        ...base,
        title: { text: "" },
        automargin: false,
        ticklabelposition: "inside" as const,
        tickfont: { ...base?.tickfont, size: 9 },
    });
    return {
        ...layout,
        showlegend: false,
        title: {
            ...layout.title,
            font: { ...layout.title?.font, size: 11 },
        },
        // No margins: the plot fills the whole tile, title and tick labels sit on top of it.
        margin: { t: 0, b: 0, l: 0, r: 0, pad: 0 },
        annotations: incoming.annotations ?? [],
        xaxis: axis(layout.xaxis),
        yaxis: axis(layout.yaxis),
        ...(layout.yaxis2 ? { yaxis2: axis(layout.yaxis2) } : {}),
    };
}

function isScatter(trace: Data): trace is Partial<Plotly.ScatterData> {
    return !trace.type || trace.type === "scatter";
}
function buildData(): Data[] {
    return structuredClone(toRaw(figure.value.data ?? [])).map(trace => {
        // Bars get the same tooltip as the lines, in place of Plotly's own.
        if (!isScatter(trace)) {
            const template: unknown = Reflect.get(trace, "hovertemplate");
            return { ...trace, hoverinfo: "none", hovertemplate: undefined, meta: { tooltipTemplate: template } };
        }
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
            mode: isolated.some(Boolean) ? "lines+markers" : "lines",
            marker: { ...scatter.marker, size: isolated },
            name: scatter.name?.replace(/:\s*(Ensemble-Median|Median|Einzelprognose|Prognose)/g, ""),
            hoverinfo: scatter.hoverinfo === "skip" ? "skip" : "none",
            hovertemplate: undefined,
            meta: { tooltipTemplate: scatter.hovertemplate },
            line: {
                ...scatter.line,
                width: scatter.line?.width === 0 ? 0 : props.keepLineWidths ? (scatter.line?.width ?? 1.5) : 1.5,
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

// The selected position is an SVG overlay, not extra Plotly traces: moving a trace's x/y is a
// calc-level restyle (supplyDefaults, calcdata, a redraw of the whole figure), and with three
// charts following every map hover that stalled the page.
const selectionDots = ref<{ cx: number; cy: number; color: string }[]>([]);
const selectionRule = ref<{ x: number; y1: number; y2: number } | undefined>(undefined);
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
    p2c: (pixel: number) => number;
}
function isPlotAxis(value: unknown): value is PlotAxis {
    return (
        typeof value === "object" &&
        value !== null &&
        typeof Reflect.get(value, "_offset") === "number" &&
        typeof Reflect.get(value, "_length") === "number" &&
        typeof Reflect.get(value, "c2p") === "function" &&
        typeof Reflect.get(value, "p2c") === "function"
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

/**
 * The y pixel (inside the subplot) of the curve Plotly drew for a trace, at `px` (inside the
 * subplot). Read off the rendered path, so the dot sits on the spline between two samples
 * rather than on the straight line through them. Undefined where the path cannot be measured
 * (jsdom) or does not reach `px`; the caller then interpolates the data.
 */
function renderedY(el: HTMLElement, traceIndex: number, px: number): number | undefined {
    const calcdata: unknown = Reflect.get(el, "calcdata");
    const traceData: unknown = Array.isArray(calcdata) ? calcdata[traceIndex] : undefined;
    const first: unknown = Array.isArray(traceData) ? traceData[0] : undefined;
    const node3: unknown = typeof first === "object" && first !== null ? Reflect.get(first, "node3") : undefined;
    const nodeFn: unknown = typeof node3 === "object" && node3 !== null ? Reflect.get(node3, "node") : undefined;
    const group: unknown = typeof nodeFn === "function" ? nodeFn.call(node3) : undefined;
    if (!(group instanceof Element)) return undefined;
    for (const path of group.querySelectorAll<SVGPathElement>("path.js-line")) {
        if (typeof path.getTotalLength !== "function" || typeof path.getPointAtLength !== "function") return undefined;
        const total = path.getTotalLength();
        if (!(total > 0)) continue;
        if (px < path.getPointAtLength(0).x || px > path.getPointAtLength(total).x) continue;
        // The lines run left to right, so x grows with the length along the path.
        let lo = 0;
        let hi = total;
        for (let i = 0; i < 20; i++) {
            const mid = (lo + hi) / 2;
            if (path.getPointAtLength(mid).x < px) lo = mid;
            else hi = mid;
        }
        const y = path.getPointAtLength((lo + hi) / 2).y;
        return Number.isFinite(y) ? y : undefined;
    }
    return undefined;
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
    const cursor = props.cursorX;
    const mainX = el && ready && !disposed ? plotAxis(el, "x", undefined) : undefined;
    const mainY = el && ready && !disposed ? plotAxis(el, "y", undefined) : undefined;
    const ruleX = mainX && cursor !== undefined ? toPixel(mainX, cursor) : undefined;
    if (!el || !mainX || !mainY || cursor === undefined || ruleX === undefined) {
        selectionDots.value = [];
        selectionRule.value = undefined;
        return;
    }
    selectionRule.value = { x: ruleX, y1: mainY._offset, y2: mainY._offset + mainY._length };
    // Sources are the drawn lines only; bands and invisible helper traces carry no dot.
    selectionDots.value = el.data.flatMap((trace, traceIndex) => {
        if (
            !isScatter(trace) ||
            trace.hoverinfo === "skip" ||
            trace.line?.width === 0 ||
            trace.visible === false ||
            trace.visible === "legendonly" ||
            (props.cursorGroup !== undefined && trace.legendgroup !== props.cursorGroup)
        ) {
            return [];
        }
        const point = seriesPointAt(trace, cursor);
        const xaxis = plotAxis(el, "x", trace.xaxis);
        const yaxis = plotAxis(el, "y", trace.yaxis);
        if (!point || !xaxis || !yaxis) return [];
        const cx = toPixel(xaxis, point.x);
        const drawn = cx === undefined ? undefined : renderedY(el, traceIndex, cx - xaxis._offset);
        const cy =
            drawn !== undefined && drawn >= 0 && drawn <= yaxis._length
                ? yaxis._offset + drawn
                : toPixel(yaxis, point.y);
        const color = typeof trace.line?.color === "string" ? trace.line.color : "#2f7fd8";
        return cx === undefined || cy === undefined ? [] : [{ cx, cy, color }];
    });
}

watch(() => props.cursorX, scheduleSelection);

async function render() {
    // Plotly measures label text during layout; measuring the fallback font before Lexend
    // has loaded leaves ticks and legend entries mis-sized.
    // (jsdom has no document.fonts)
    const fontDocument: Partial<Document> = document;
    ready = false;
    // The old dots sit at the old figure's pixels; drop them until the new layout exists.
    selectionDots.value = [];
    selectionRule.value = undefined;
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
        compact.value = needsCompact(chart.value);
        narrow.value = isNarrow(chart.value);
        layout.value = buildLayout();
        resizeObserver = new ResizeObserver(() => {
            const el = chart.value;
            if (!el) return;
            if (needsCompact(el) === compact.value && isNarrow(el) === narrow.value) {
                Plotly.Plots.resize(el);
                return;
            }
            // Crossed a threshold: swap layouts. render() resizes as well.
            compact.value = needsCompact(el);
            narrow.value = isNarrow(el);
            hideTooltip();
            layout.value = buildLayout();
            void render();
        });
        resizeObserver.observe(chart.value);
    }
    await render();
    if (disposed) return;
    chart.value?.on("plotly_hover", showPoint);
    chart.value?.on("plotly_click", clickPoint);
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
    if (cursorFrame !== undefined) cancelAnimationFrame(cursorFrame);
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
    <q-card
        flat
        class="chart-shell"
        :class="{ 'chart-shell--compact': compact }"
        @pointermove="trackPointer"
        @pointerleave="hideTooltip"
        @keydown.esc="hideTooltip"
    >
        <div ref="chartRef" class="chart-plot" />
        <svg class="chart-selection" aria-hidden="true">
            <line
                v-if="selectionRule"
                data-testid="chart-selection-rule"
                :x1="selectionRule.x"
                :x2="selectionRule.x"
                :y1="selectionRule.y1"
                :y2="selectionRule.y2"
                :stroke="selectionStroke"
                stroke-opacity="0.35"
                stroke-width="1"
            />
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
        <q-tooltip v-if="tooltip" role="tooltip">
            <q-item-label caption>{{ tooltip.time }}</q-item-label>
            <q-item-label>{{ tooltip.label }}</q-item-label>
            <q-item-label>{{ tooltip.value }}</q-item-label>
        </q-tooltip>
    </q-card>
</template>

<style scoped>
.chart-shell {
    position: relative;
    height: 100%;
    min-width: 0;
    min-height: 0;
    overflow: hidden;
}
/* Out of flow: Plotly sizes its SVG in pixels, which must never feed back into the card's height. */
.chart-plot {
    position: absolute;
    inset: 0;
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
</style>
