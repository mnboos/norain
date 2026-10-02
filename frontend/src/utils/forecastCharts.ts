/**
 * The forecast charts along the ride: temperature with the precipitation as bars, and headwind.
 *
 * Built here from the samples the job result already carries, so the charts need no request of
 * their own. Only the data changes between forecasts; the design is all in this file.
 * NiceChart.vue adds the theme (colours of the chrome, fonts, margins, legend position).
 */
import type { Data, Layout } from "plotly.js";

import { t, tp } from "@/i18n";

/** The fields of a forecast sample the charts read. */
export interface ChartSample {
    elapsedS: number;
    eta: string;
    temp: number;
    /** Wind chill at riding speed; missing without ride timing and on older forecasts. */
    feltTemp?: number | null;
    rainRateMmH?: number | null;
    pop?: number | null;
    headwind?: number | null;
    uncertainty?: {
        metrics: Record<string, { p10?: number | null; median?: number | null; p90?: number | null } | undefined>;
    } | null;
}

export interface ChartFigure {
    data: Data[];
    layout: Partial<Layout>;
}

// Series hues at mid lightness, so one hex reads on both the light (#ffffff) and the dark
// (#1c2533) card: a dark red for temperature, a blue for rain and wind.
const RED = "#b0404e";
const BLUE = "#3a70b8";

// Visual-only smoothing: the curve still passes through every real sample, but a spline can
// slightly over/undershoot between two points at abrupt changes. Moderate smoothing keeps that small.
const SMOOTH = { shape: "spline", smoothing: 0.7 } as const;

interface Series {
    /** The key in `uncertainty.metrics`, also the legend group. */
    metric: string;
    /** Legend name: short. */
    name: string;
    /** Hover label: may be more precise. */
    label: string;
    color: string;
    unit: string;
    dash: "solid" | "dot";
    value: (sample: ChartSample) => number | null | undefined;
}

function fill(color: string, alpha: number): string {
    const [r, g, b] = [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16));
    return `rgba(${r},${g},${b},${alpha})`;
}

/** `[sample index, "HH:MM"]`: NiceChart selects the sample by the index and shows the time. */
function customdata(samples: readonly ChartSample[]): [number, string][] {
    return samples.map((sample, index) => [index, sample.eta.slice(11, 16)]);
}

function minutes(samples: readonly ChartSample[]): number[] {
    return samples.map(sample => sample.elapsedS / 60);
}

/**
 * The ensemble's spread as a band around the line: `value + (p10 − median)` to
 * `value + (p90 − median)`. It shows how uncertain the value is, recentred on the value the chart
 * draws (the main run near now, the ensemble's centre from 72 h). So it is the ensemble's
 * spread, not its absolute p10–p90 range, which the details panel shows. Empty without ensemble data.
 */
function spreadTraces(samples: readonly ChartSample[], series: Series): Data[] {
    const bounds = samples.map(sample => {
        const value = series.value(sample);
        const range = sample.uncertainty?.metrics[series.metric];
        if (value == null || range?.median == null || range.p10 == null || range.p90 == null) return null;
        return { p10: value + range.p10 - range.median, p90: value + range.p90 - range.median };
    });
    if (!bounds.some(Boolean)) return [];
    const x = minutes(samples);

    // Separate each contiguous run: Plotly's filled polygons must not bridge missing data.
    const runs: number[][] = [];
    let run: number[] = [];
    bounds.forEach((bound, index) => {
        if (bound) {
            run.push(index);
        } else if (run.length) {
            runs.push(run);
            run = [];
        }
    });
    if (run.length) runs.push(run);

    return runs.flatMap(indices =>
        (["p10", "p90"] as const).map((key): Data => ({
            type: "scatter",
            x: indices.map(i => x[i] ?? null),
            y: indices.map(i => bounds[i]?.[key] ?? null),
            mode: "lines",
            line: { width: 0, ...SMOOTH },
            fill: key === "p90" ? "tonexty" : "none",
            fillcolor: fill(series.color, 0.16),
            // One legend entry per metric toggles the line and its band together.
            legendgroup: series.metric,
            showlegend: false,
            hoverinfo: "skip",
        })),
    );
}

/** The sample's value as a line: the main run near now, moving onto the ensemble's centre by 72 h. */
function forecastTrace(samples: readonly ChartSample[], series: Series): Data {
    return {
        type: "scatter",
        x: minutes(samples),
        y: samples.map(sample => series.value(sample) ?? null),
        customdata: customdata(samples),
        mode: "lines+markers",
        marker: { size: 4 },
        line: { dash: series.dash, width: 2, color: series.color, ...SMOOTH },
        connectgaps: false,
        name: series.name,
        legendgroup: series.metric,
        showlegend: true,
        hovertemplate: `${t("common.clock", { time: "%{customdata[1]}" })} · %{y:.1f} ${series.unit}<extra>${series.label}</extra>`,
    };
}

/** The band first, so the line is drawn on top of it. */
function seriesTraces(samples: readonly ChartSample[], series: Series): Data[] {
    return [...spreadTraces(samples, series), forecastTrace(samples, series)];
}

/** The layout every chart shares: a title pinned top-left, the ride time along x. */
function baseLayout(title: string, unit: string, profile?: string | null): Partial<Layout> {
    // automargin lets Plotly grow the margins to fit the tick labels.
    return {
        title: {
            text: title,
            font: { size: 14 },
            x: 0,
            xref: "paper",
            xanchor: "left",
            y: 1,
            yref: "container",
            yanchor: "top",
            pad: { t: 10 },
        },
        autosize: true,
        hovermode: "closest",
        showlegend: true,
        xaxis: { title: { text: tp(profile, "charts.axis.rideMinutes"), standoff: 4 }, zeroline: false, automargin: true },
        yaxis: { title: { text: unit, standoff: 15 }, zeroline: true, automargin: true },
        legend: {
            orientation: "h",
            font: { size: 11 },
            itemwidth: 30,
            tracegroupgap: 0,
            bgcolor: "rgba(0,0,0,0)",
        },
    };
}

/** Temperature as a line, and the main run's rain rate as bars on a second axis. */
function temperatureChart(samples: readonly ChartSample[], profile?: string | null): ChartFigure {
    const series: Series = {
        metric: "temperature",
        name: t("charts.temperature"),
        label: t("charts.temperature"),
        color: RED,
        unit: "°C",
        dash: "solid",
        value: sample => sample.temp,
    };
    const layout = baseLayout(t("charts.temperature"), "°C", profile);
    const data = seriesTraces(samples, series);
    if (samples.some(sample => sample.feltTemp != null)) {
        const felt: Series = {
            metric: "felt",
            name: t("charts.felt"),
            label: tp(profile, "charts.feltLong"),
            color: RED,
            unit: "°C",
            dash: "dot",
            value: sample => sample.feltTemp,
        };
        data.push(forecastTrace(samples, felt));
    }
    if (samples.some(sample => sample.rainRateMmH != null)) {
        data.unshift({
            type: "bar",
            x: minutes(samples),
            y: samples.map(sample => sample.rainRateMmH ?? null),
            customdata: customdata(samples),
            name: t("charts.precipitation"),
            legendgroup: "precipitation",
            marker: { color: BLUE, opacity: 0.45 },
            yaxis: "y2",
            hovertemplate: `${t("common.clock", { time: "%{customdata[1]}" })} · %{y:.1f} mm/h<extra>${t("charts.precipitation")}</extra>`,
        });
        // Rain can't be negative, and a dry ride would otherwise autorange to -1..1 mm/h. The
        // axis is at least 0..1 so a drizzle does not fill the whole chart height.
        const peak = Math.max(1, ...samples.map(sample => sample.rainRateMmH ?? 0));
        layout.yaxis2 = {
            overlaying: "y",
            side: "right",
            range: [0, peak * 1.1],
            tickformat: ".1f",
            title: { text: "mm/h", standoff: 15 },
            zeroline: false,
            automargin: true,
        };
    }
    return { data, layout };
}

function headwindChart(samples: readonly ChartSample[], profile?: string | null): ChartFigure {
    const series: Series = {
        metric: "headwind",
        name: t("charts.headwindTailwind"),
        label: t("charts.headwindTailwindShort"),
        color: BLUE,
        unit: "km/h",
        dash: "solid",
        value: sample => sample.headwind,
    };
    return { data: seriesTraces(samples, series), layout: baseLayout(t("charts.headwind"), "km/h", profile) };
}

export type ChartKind = "temperature" | "headwind";

/** Temperature (with precipitation) and headwind, in that order; none without samples. The
 * routing profile words the axes (a hike walks, it does not ride). */
export function forecastCharts(samples: readonly ChartSample[], profile?: string | null): ChartFigure[] {
    if (!samples.length) return [];
    return [temperatureChart(samples, profile), headwindChart(samples, profile)];
}

/** One of the charts; undefined without samples. */
export function forecastChart(
    kind: ChartKind,
    samples: readonly ChartSample[],
    profile?: string | null,
): ChartFigure | undefined {
    if (!samples.length) return undefined;
    return kind === "temperature" ? temperatureChart(samples, profile) : headwindChart(samples, profile);
}
