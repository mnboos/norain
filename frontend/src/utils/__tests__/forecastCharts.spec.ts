import { describe, expect, it } from "vitest";
import type { Data, ScatterData } from "plotly.js";
import { forecastCharts, type ChartFigure, type ChartSample } from "../forecastCharts";

type Metrics = NonNullable<ChartSample["uncertainty"]>["metrics"];

function spread(median: number): Metrics[string] {
    return { p10: median - 2, median, p90: median + 2 };
}

function sample(elapsedS: number, extra: Partial<ChartSample> = {}): ChartSample {
    return {
        elapsedS,
        eta: `2026-09-18T10:${String(elapsedS / 60).padStart(2, "0")}:00+02:00`,
        temp: 12,
        rainRateMmH: 0.5,
        headwind: 4,
        ...extra,
    };
}

function withSpread(elapsedS: number, extra: Partial<ChartSample> = {}): ChartSample {
    const metrics: Metrics = { temperature: spread(15), precipitation: spread(3), headwind: spread(6) };
    return sample(elapsedS, { uncertainty: { metrics }, ...extra });
}

/** The line traces; the rain bars are the only other kind. */
function scatters(chart: ChartFigure | undefined): Partial<ScatterData>[] {
    return (chart?.data ?? []).filter((trace: Data): trace is Partial<ScatterData> => trace.type === "scatter");
}

function bars(chart: ChartFigure | undefined): Data[] {
    return (chart?.data ?? []).filter(trace => trace.type === "bar");
}

/** The two charts, failing the test when one is missing. */
function charts(samples: ChartSample[]): [ChartFigure, ChartFigure] {
    const [temperature, headwind] = forecastCharts(samples);
    if (!temperature || !headwind) throw new Error("expected two charts");
    return [temperature, headwind];
}

const isUpper = (trace: Partial<ScatterData>) => trace.fill === "tonexty";
const isLower = (trace: Partial<ScatterData>) => trace.fill === "none";
const isLine = (trace: Partial<ScatterData>) => trace.line?.width === 2;

describe("forecastCharts", () => {
    it("draws nothing without samples", () => {
        expect(forecastCharts([])).toEqual([]);
    });

    it("adds the felt temperature as its own line only when the samples carry it", () => {
        const [plain] = charts([sample(0), sample(300)]);
        expect(scatters(plain).map(trace => trace.name)).not.toContain("Gefühlt");
        const [temperature] = charts([sample(0, { feltTemp: 8 }), sample(300, { feltTemp: 9 })]);
        const felt = scatters(temperature).find(trace => trace.name === "Gefühlt");
        expect(felt?.y).toEqual([8, 9]);
        expect(felt?.showlegend).toBe(true);
        expect(felt?.line?.dash).toBe("dot");
    });

    it("draws temperature with the rain as bars, and headwind", () => {
        const [temperature, headwind] = charts([withSpread(0), withSpread(300)]);
        expect(temperature.layout.title).toMatchObject({ text: "Temperatur" });
        expect(headwind.layout.title).toMatchObject({ text: "Gegenwind" });
        expect(bars(temperature)).toHaveLength(1);
        expect(bars(headwind)).toHaveLength(0);
        expect(scatters(headwind)).toHaveLength(headwind.data.length);
    });

    it("draws one line per metric, with the ensemble spread recentred on it", () => {
        // The ensemble median is 15 °C / 6 km/h, the line 12 °C / 4 km/h: the band keeps the
        // spread (±2) but sits around the line, and no median line is drawn.
        const [temperature, headwind] = charts([withSpread(0), withSpread(300)]);
        for (const [chart, value] of [[temperature, 12], [headwind, 4]] as const) {
            const traces = scatters(chart);
            expect(traces.filter(isLine)).toHaveLength(1);
            expect(traces.find(isLine)?.y).toEqual([value, value]);
            expect(traces.find(isLine)?.line?.dash).toBe("solid");
            expect(traces.find(isLower)?.y).toEqual([value - 2, value - 2]);
            expect(traces.find(isUpper)?.y).toEqual([value + 2, value + 2]);
        }
    });

    it("keeps an asymmetric spread asymmetric", () => {
        const metrics = { temperature: { p10: 14, median: 15, p90: 19 } };
        const [temperature] = charts([sample(0, { uncertainty: { metrics } })]);
        expect(scatters(temperature).find(isLower)?.y).toEqual([11]);
        expect(scatters(temperature).find(isUpper)?.y).toEqual([16]);
    });

    it("keeps gaps, ride minutes and sample indices", () => {
        const [temperature] = charts([withSpread(0), sample(300, { rainRateMmH: null }), withSpread(600)]);
        const line = scatters(temperature).find(isLine);
        expect(line?.x).toEqual([0, 5, 10]);
        expect(line?.customdata).toEqual([
            [0, "10:00"],
            [1, "10:05"],
            [2, "10:10"],
        ]);
        // Two runs, so two bands: the fill must not bridge the sample without a spread.
        expect(scatters(temperature).filter(isUpper)).toHaveLength(2);

        expect(bars(temperature)[0]).toMatchObject({ x: [0, 5, 10], y: [0.5, null, 0.5], yaxis: "y2" });
        expect(temperature.layout.yaxis2).toMatchObject({ overlaying: "y", side: "right" });
    });

    it("gives each legend group one entry", () => {
        for (const chart of charts([withSpread(0), withSpread(300)])) {
            const shown = chart.data
                .filter(trace => !("showlegend" in trace) || trace.showlegend !== false)
                .map(trace => ("legendgroup" in trace ? trace.legendgroup : undefined));
            expect(new Set(shown).size).toBe(shown.length);
        }
    });

    it("draws only the line, with its own legend entry, without an ensemble", () => {
        // A free account's samples carry no uncertainty.
        const all = charts([sample(0), sample(300)]);
        for (const chart of all) {
            const traces = scatters(chart);
            expect(traces.filter(isUpper)).toHaveLength(0);
            expect(traces).toHaveLength(1);
            expect(traces[0]?.showlegend).toBe(true);
        }
        expect(scatters(all[1])[0]?.y).toEqual([4, 4]);
    });

    it("starts the rain axis at zero and leaves room for a drizzle", () => {
        const [temperature] = charts([sample(0, { rainRateMmH: 0.2 })]);
        expect(temperature.layout.yaxis2?.range?.[0]).toBe(0);
        expect(temperature.layout.yaxis2?.range?.[1]).toBeGreaterThanOrEqual(1);
    });

    it("draws no rain bars when no sample has a rain rate", () => {
        const [temperature] = charts([sample(0, { rainRateMmH: null })]);
        expect(bars(temperature)).toHaveLength(0);
        expect(temperature.layout.yaxis2).toBeUndefined();
    });
});
