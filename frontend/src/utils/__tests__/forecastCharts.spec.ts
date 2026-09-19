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

const isBand = (trace: Partial<ScatterData>) => trace.fill === "tonexty";
const isMedian = (trace: Partial<ScatterData>) => trace.line?.width === 2;
const isSingle = (trace: Partial<ScatterData>) => trace.line?.dash != null;

describe("forecastCharts", () => {
    it("draws nothing without samples", () => {
        expect(forecastCharts([])).toEqual([]);
    });

    it("draws temperature with the rain as bars, and headwind", () => {
        const [temperature, headwind] = charts([withSpread(0), withSpread(300)]);
        expect(temperature.layout.title).toMatchObject({ text: "Temperatur" });
        expect(headwind.layout.title).toMatchObject({ text: "Gegenwind" });
        expect(bars(temperature)).toHaveLength(1);
        expect(bars(headwind)).toHaveLength(0);
        expect(scatters(headwind)).toHaveLength(headwind.data.length);
    });

    it("keeps gaps, ride minutes and sample indices", () => {
        const [temperature] = charts([withSpread(0), sample(300, { rainRateMmH: null }), withSpread(600)]);
        const median = scatters(temperature).find(isMedian);
        expect(median?.x).toEqual([0, 5, 10]);
        expect(median?.y).toEqual([15, null, 15]);
        expect(median?.customdata).toEqual([
            [0, "10:00"],
            [1, "10:05"],
            [2, "10:10"],
        ]);
        // Two runs, so two bands: the fill must not bridge the missing sample.
        expect(scatters(temperature).filter(isBand)).toHaveLength(2);

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

    it("draws only the single forecast, with its own legend entry, without an ensemble", () => {
        // A free account's samples carry no uncertainty.
        const all = charts([sample(0), sample(300)]);
        for (const chart of all) {
            const traces = scatters(chart);
            expect(traces.filter(isBand)).toHaveLength(0);
            expect(traces.filter(isMedian)).toHaveLength(0);
            const single = traces.filter(isSingle);
            expect(single).toHaveLength(1);
            expect(single[0]?.showlegend).toBe(true);
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
