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

/** Every trace here is a scatter; narrowing keeps the tests free of type assertions. */
function scatters(chart: ChartFigure | undefined): Partial<ScatterData>[] {
    return (chart?.data ?? []).filter((trace: Data): trace is Partial<ScatterData> => trace.type === "scatter");
}

/** The three charts, failing the test when one is missing. */
function charts(samples: ChartSample[]): [ChartFigure, ChartFigure, ChartFigure] {
    const [temperature, precipitation, headwind] = forecastCharts(samples);
    if (!temperature || !precipitation || !headwind) throw new Error("expected three charts");
    return [temperature, precipitation, headwind];
}

const isBand = (trace: Partial<ScatterData>) => trace.fill === "tonexty";
const isMedian = (trace: Partial<ScatterData>) => trace.line?.width === 2;
const isSingle = (trace: Partial<ScatterData>) => trace.line?.dash === "dot";

describe("forecastCharts", () => {
    it("draws nothing without samples", () => {
        expect(forecastCharts([])).toEqual([]);
    });

    it("draws temperature, precipitation and headwind with scatter traces only", () => {
        const all = charts([withSpread(0), withSpread(300)]);
        expect(all.map(chart => chart.layout.title)).toMatchObject([
            { text: "Temperatur" },
            { text: "Niederschlag" },
            { text: "Gegenwind / Rückenwind" },
        ]);
        for (const chart of all) {
            expect(scatters(chart)).toHaveLength(chart.data.length);
        }
    });

    it("keeps gaps, ride minutes and sample indices", () => {
        const [temperature, precipitation] = charts([
            withSpread(0, { pop: 0.5 }),
            sample(300),
            withSpread(600, { pop: 0 }),
        ]);
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

        const pop = scatters(precipitation).find(trace => trace.yaxis === "y2");
        expect(pop?.y).toEqual([50, null, 0]);
        expect(precipitation.layout.showlegend).toBe(true);
        expect(precipitation.layout.yaxis2).toMatchObject({ overlaying: "y", range: [0, 100] });
    });

    it("gives each legend group one entry, and a lone series none", () => {
        const all = charts([withSpread(0, { pop: 0.2 }), withSpread(300, { pop: 0.4 })]);
        expect(all[0].layout.showlegend).toBe(false);
        for (const chart of all) {
            const shown = scatters(chart)
                .filter(trace => trace.showlegend !== false)
                .map(trace => trace.legendgroup);
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
        expect(all[1].layout.showlegend).toBe(false);
        expect(scatters(all[2])[0]?.y).toEqual([4, 4]);
    });

    it("keeps the rain axis at zero and above", () => {
        const [, precipitation] = charts([sample(0)]);
        expect(precipitation.layout.yaxis).toMatchObject({ rangemode: "nonnegative", zeroline: false });
    });
});
