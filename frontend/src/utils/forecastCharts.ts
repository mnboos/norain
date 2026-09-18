/**
 * The forecast charts: temperature, precipitation and headwind along the ride.
 *
 * Built here from the samples the job result already carries, so the charts need no request of
 * their own. Only the data changes between forecasts; the design is all in this file.
 * NiceChart.vue adds the theme (colours of the chrome, fonts, margins, legend position).
 */
import type { Data, Layout } from "plotly.js";

/** The fields of a forecast sample the charts read. */
export interface ChartSample {
    elapsedS: number;
    eta: string;
    temp: number;
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

// Cool, orange-free series hues at mid lightness, so one hex reads on both the light (#ffffff) and
// the dark (#1c2533) card. The map markers and the light theme (utils/theme.ts) reuse these hues.
const TEAL = "#1a9e8f";
const ROSE = "#d24d78";
const BLUE = "#2f7fd8";

// Visual-only smoothing: the curve still passes through every real sample, but a spline can
// slightly over/undershoot between two points at abrupt changes. Moderate smoothing keeps that small.
const SMOOTH = { shape: "spline", smoothing: 0.7 } as const;

type Bound = "p10" | "median" | "p90";

interface Series {
    /** The key in `uncertainty.metrics`, also the legend group. */
    metric: string;
    /** Legend name: short. */
    name: string;
    /** Hover label: may be more precise. */
    label: string;
    color: string;
    unit: string;
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

/** Band + median for one metric; empty when there is no ensemble data to draw. */
function ensembleTraces(samples: readonly ChartSample[], series: Series): Data[] {
    const ranges = samples.map(sample => sample.uncertainty?.metrics[series.metric]);
    const present = ranges.map(range => range?.median != null);
    if (!present.some(Boolean)) return [];
    const x = minutes(samples);
    const at = (index: number, bound: Bound) => ranges[index]?.[bound] ?? null;

    // Separate each contiguous run: Plotly's filled polygons must not bridge missing data.
    const runs: number[][] = [];
    let run: number[] = [];
    present.forEach((has, index) => {
        if (has) {
            run.push(index);
        } else if (run.length) {
            runs.push(run);
            run = [];
        }
    });
    if (run.length) runs.push(run);

    const traces: Data[] = runs.flatMap(indices =>
        (["p10", "p90"] as const).map((bound): Data => ({
            type: "scatter",
            x: indices.map(i => x[i] ?? null),
            y: indices.map(i => at(i, bound)),
            mode: "lines",
            line: { width: 0, ...SMOOTH },
            fill: bound === "p90" ? "tonexty" : "none",
            fillcolor: fill(series.color, 0.16),
            legendgroup: series.metric,
            showlegend: false,
            hoverinfo: "skip",
        })),
    );
    // The legend names only the metric: the caption above the charts explains band/median/dotted,
    // and one entry per metric toggles its whole group (band, median and single forecast).
    traces.push({
        type: "scatter",
        x,
        y: samples.map((_, index) => at(index, "median")),
        customdata: customdata(samples),
        mode: "lines+markers",
        marker: { size: 4 },
        line: { color: series.color, width: 2, ...SMOOTH },
        connectgaps: false,
        name: series.name,
        legendgroup: series.metric,
        hovertemplate: `%{customdata[1]} Uhr · %{y:.1f} ${series.unit}<extra>${series.label}: Median</extra>`,
    });
    return traces;
}

/** The main run's value as a dotted line. It only gets a legend entry when there is no median. */
function singleForecastTrace(samples: readonly ChartSample[], series: Series, withEnsemble: boolean): Data {
    return {
        type: "scatter",
        x: minutes(samples),
        y: samples.map(sample => series.value(sample) ?? null),
        customdata: customdata(samples),
        mode: "lines+markers",
        marker: { size: 3 },
        line: { dash: "dot", width: 1.5, color: series.color, ...SMOOTH },
        connectgaps: false,
        name: series.name,
        legendgroup: series.metric,
        showlegend: !withEnsemble,
        hovertemplate: `%{customdata[1]} Uhr · %{y:.1f} ${series.unit}<extra>${series.label}: Einzelprognose</extra>`,
    };
}

function seriesTraces(samples: readonly ChartSample[], series: Series): Data[] {
    const ensemble = ensembleTraces(samples, series);
    return [...ensemble, singleForecastTrace(samples, series, ensemble.length > 0)];
}

/** The layout every chart shares: a title pinned top-left, the ride time along x. */
function baseLayout(title: string, unit: string): Partial<Layout> {
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
        showlegend: false,
        xaxis: { title: { text: "Fahrzeit (min)", standoff: 4 }, zeroline: false, automargin: true },
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

function temperatureChart(samples: readonly ChartSample[]): ChartFigure {
    const series: Series = {
        metric: "temperature",
        name: "Temperatur",
        label: "Temperatur",
        color: ROSE,
        unit: "°C",
        value: sample => sample.temp,
    };
    // A lone series needs no legend: the chart title already names it.
    return { data: seriesTraces(samples, series), layout: baseLayout("Temperatur", "°C") };
}

function precipitationChart(samples: readonly ChartSample[]): ChartFigure {
    const series: Series = {
        metric: "precipitation",
        name: "Niederschlag",
        label: "Niederschlag",
        color: BLUE,
        unit: "mm/h",
        value: sample => sample.rainRateMmH,
    };
    const base = baseLayout("Niederschlag", "mm/h");
    // Rain rate can't be negative: without rangemode an all-dry route autoranges to -1..1 mm/h, and a
    // spline dipping below 0 near a rain onset would be drawn as negative rain. No zeroline: the
    // range starts at 0, so it would only redraw the plot's bottom border.
    const layout: Partial<Layout> = { ...base, yaxis: { ...base.yaxis, zeroline: false, rangemode: "nonnegative" } };
    const data = seriesTraces(samples, series);
    if (samples.some(sample => sample.pop != null)) {
        data.push({
            type: "scatter",
            x: minutes(samples),
            y: samples.map(sample => (sample.pop != null ? sample.pop * 100 : null)),
            customdata: customdata(samples),
            name: "Regenrisiko (%)",
            legendgroup: "pop",
            mode: "lines+markers",
            marker: { size: 3 },
            connectgaps: false,
            line: { color: TEAL, dash: "dot", width: 1.5, ...SMOOTH },
            yaxis: "y2",
            hovertemplate: "%{customdata[1]} Uhr · %{y:.0f}%<extra>Regenrisiko am Punkt</extra>",
        });
        layout.showlegend = true;
        // Plotly otherwise syncs an overlaying axis's ticks to the primary axis, giving labels like
        // 23.7 / 71.3 %; round percentage steps read better. No zeroline: both axes start at 0, so it
        // would be drawn twice on the same pixel row.
        layout.yaxis2 = {
            overlaying: "y",
            side: "right",
            range: [0, 100],
            title: { text: "%", standoff: 15 },
            tickmode: "linear",
            dtick: 25,
            zeroline: false,
            automargin: true,
        };
    }
    return { data, layout };
}

function headwindChart(samples: readonly ChartSample[]): ChartFigure {
    const series: Series = {
        metric: "headwind",
        name: "Gegenwind",
        label: "Gegen-(+)/Rückenwind(−)",
        color: ROSE,
        unit: "km/h",
        value: sample => sample.headwind,
    };
    return { data: seriesTraces(samples, series), layout: baseLayout("Gegenwind / Rückenwind", "km/h") };
}

/** Temperature, precipitation and headwind, in that order; none without samples. */
export function forecastCharts(samples: readonly ChartSample[]): ChartFigure[] {
    if (!samples.length) return [];
    return [temperatureChart(samples), precipitationChart(samples), headwindChart(samples)];
}
