import { describe, expect, it } from "vitest";

import type { ForecastSampleOut } from "@norain/api/models";
import {
    NO_DATA_COLOR,
    YLORRD_8,
    gradientStops,
    rainLevelColor,
    sampleProgress,
    scoreBand,
    scoreColor,
} from "@/utils/rideQuality";

describe("rainLevelColor", () => {
    it("uses only the rain severity", () => {
        expect(rainLevelColor("stark")).toBe("#c10015");
        expect(rainLevelColor("mässig")).toBe("#f2c037");
        expect(rainLevelColor("leicht")).toBe("#3498db");
        expect(rainLevelColor(null)).toBe(NO_DATA_COLOR);
    });
});

function sample(over: Partial<ForecastSampleOut> = {}): ForecastSampleOut {
    return {
        lat: 47.5,
        lon: 9.25,
        elapsedS: 0,
        eta: "2026-09-12T08:00:00",
        rainMm: 0,
        rainRateMmH: 0,
        temp: 18,
        windSpeed: 5,
        windDir: 180,
        headwind: 0,
        crosswind: 0,
        weatherDesc: "Klar",
        ...over,
    };
}

/** Indexed access under `noUncheckedIndexedAccess`, without littering the tests with `!`. */
function at<T>(list: readonly T[], i: number): T {
    const v = list[i];
    if (v === undefined) throw new Error(`no element at index ${i}`);
    return v;
}

/** The server's score of each sample, or null where it could not score one - what gradientStops consumes. */
const scoresOf = (ss: ForecastSampleOut[]) => ss.map(s => s.rideScore ?? null);

/** Unpack the flat maplibre stop list, checking it really is [number, string, ...]. */
function pairs(stops: (number | string)[]): { p: number; color: string }[] {
    const out: { p: number; color: string }[] = [];
    for (let i = 0; i < stops.length; i += 2) {
        const p = stops[i];
        const color = stops[i + 1];
        if (typeof p !== "number" || typeof color !== "string") throw new Error("malformed gradient stop list");
        out.push({ p, color });
    }
    return out;
}

const channels = (hex: string): [number, number, number] => [
    Number.parseInt(hex.slice(1, 3), 16),
    Number.parseInt(hex.slice(3, 5), 16),
    Number.parseInt(hex.slice(5, 7), 16),
];

describe("scoreColor", () => {
    it("hits the exact ramp endpoints", () => {
        expect(scoreColor(0)).toBe(at(YLORRD_8, 0));
        expect(scoreColor(1)).toBe(at(YLORRD_8, YLORRD_8.length - 1));
    });

    it("lands on the intermediate steps and interpolates between them", () => {
        const step = 1 / (YLORRD_8.length - 1);
        expect(scoreColor(step)).toBe(at(YLORRD_8, 1));
        expect(scoreColor(4 * step)).toBe(at(YLORRD_8, 4));
        const mid = scoreColor(0.5 * step);
        expect(mid).not.toBe(at(YLORRD_8, 0));
        expect(mid).not.toBe(at(YLORRD_8, 1));
    });

    it("returns the neutral grey - never a ramp step - for unknown values", () => {
        for (const v of [null, undefined, Number.NaN]) expect(scoreColor(v)).toBe(NO_DATA_COLOR);
        // Widened on purpose: as literal types TS can already prove these never overlap,
        // and that proof is the point - the grey must never collide with a ramp step.
        const ramp: readonly string[] = YLORRD_8;
        expect(ramp.includes(NO_DATA_COLOR)).toBe(false);
    });
});

describe("sampleProgress", () => {
    // A leg heading east along the equator: vertex n sits n units from the start.
    const leg = (n: number) => Array.from({ length: n }, (_, i) => [i * 0.01, 0]);

    it("measures distance, not time - so uneven speed no longer shifts the colours", () => {
        const samples = [
            sample({ lon: 0, lat: 0, elapsedS: 0 }),
            // Halfway by distance, but 80% of the way by time (a slow climb, then a descent).
            sample({ lon: 0.02, lat: 0, elapsedS: 800 }),
            sample({ lon: 0.04, lat: 0, elapsedS: 1000 }),
        ];
        const progress = sampleProgress(leg(5), samples, 1000);
        expect(at(progress, 0)).toBeCloseTo(0, 6);
        expect(at(progress, 1)).toBeCloseTo(0.5, 6);
        expect(at(progress, 2)).toBeCloseTo(1, 6);
        // The old time-fraction approximation would have said 0.8 for the middle sample.
        expect(at(progress, 1)).not.toBeCloseTo(0.8, 2);
    });

    it("keeps the legs apart on an out-and-back route", () => {
        // Out to 0.03, then back to 0 - every return vertex coincides with an outbound one.
        const out = leg(4);
        const line = [...out, ...out.slice(0, 3).reverse()];
        const samples = [
            sample({ lon: 0, lat: 0, elapsedS: 0 }),
            sample({ lon: 0.03, lat: 0, elapsedS: 500 }), // turn-around
            sample({ lon: 0.01, lat: 0, elapsedS: 800 }), // on the way home
            sample({ lon: 0, lat: 0, elapsedS: 1000 }), // back at the start
        ];
        const progress = sampleProgress(line, samples, 1000);
        expect(at(progress, 1)).toBeCloseTo(0.5, 6);
        // Nearest-vertex matching would have snapped these back onto the outbound leg.
        expect(at(progress, 2)).toBeGreaterThan(0.5);
        expect(at(progress, 3)).toBeCloseTo(1, 6);
        for (let i = 1; i < progress.length; i++) expect(at(progress, i)).toBeGreaterThan(at(progress, i - 1));
    });

    it("falls back to the time fraction on degenerate geometry", () => {
        const samples = [sample({ elapsedS: 0 }), sample({ elapsedS: 500 }), sample({ elapsedS: 1000 })];
        const degenerate: number[][][] = [
            [],
            [[9.25, 47.5]],
            [
                [9.25, 47.5],
                [9.25, 47.5],
            ],
        ];
        for (const line of degenerate) {
            const progress = sampleProgress(line, samples, 1000);
            expect(progress).toHaveLength(3);
            expect(progress.every(Number.isFinite)).toBe(true);
        }
        expect(at(sampleProgress([], samples, 1000), 1)).toBeCloseTo(0.5, 6);
    });

    it("always returns strictly increasing values inside 0..1", () => {
        // Every sample on the same vertex, and no total time: the nastiest input there is.
        const samples = Array.from({ length: 50 }, () => sample({ lon: 0, lat: 0, elapsedS: 0 }));
        const progress = sampleProgress(leg(50), samples, 0);
        for (let i = 1; i < progress.length; i++) expect(at(progress, i)).toBeGreaterThan(at(progress, i - 1));
        expect(Math.min(...progress)).toBeGreaterThanOrEqual(0);
        expect(Math.max(...progress)).toBeLessThanOrEqual(1);
    });
});

describe("gradientStops", () => {
    it("keeps stops strictly increasing inside 0..1", () => {
        const stops = pairs(gradientStops([0, 0.5, 1], [0.1, 0.9, 0.2]));
        for (let i = 1; i < stops.length; i++) expect(at(stops, i).p).toBeGreaterThan(at(stops, i - 1).p);
        expect(at(stops, 0).p).toBeGreaterThanOrEqual(0);
        expect(at(stops, stops.length - 1).p).toBeLessThanOrEqual(1);
    });

    it("subdivides a big jump so the ramp passes through its steps instead of chording across it", () => {
        // 0.15 -> 0.75 is more than four ramp steps. One stop per sample would hand maplibre
        // two hexes and let it blend the straight sRGB chord between them.
        const stops = pairs(gradientStops([0, 1], [0.15, 0.75]));
        expect(stops.length).toBeGreaterThan(5);

        // The intermediate stops are sampled from the ramp itself, not just the endpoints.
        const expected = [0.3, 0.45, 0.6].map(s => scoreColor(s));
        for (const color of expected) {
            const [r, g, b] = channels(color);
            const near = stops.some(s => {
                const [r1, g1, b1] = channels(s.color);
                return Math.max(Math.abs(r1 - r), Math.abs(g1 - g), Math.abs(b1 - b)) < 20;
            });
            expect(near).toBe(true);
        }

        // ...and it gets there gradually: no visible lurch between neighbouring stops.
        for (let i = 1; i < stops.length; i++) {
            const [r0, g0, b0] = channels(at(stops, i - 1).color);
            const [r1, g1, b1] = channels(at(stops, i).color);
            expect(Math.max(Math.abs(r1 - r0), Math.abs(g1 - g0), Math.abs(b1 - b0))).toBeLessThan(70);
        }
    });

    it("never lets two adjacent known stops span more than one ramp step", () => {
        const stops = pairs(gradientStops([0, 0.4, 1], [0, 1, 0.3]));
        const onRamp = stops.map(s => YLORRD_8.findIndex(c => c === s.color)).filter(i => i >= 0);
        expect(onRamp.length).toBeGreaterThan(8);
        for (let i = 1; i < onRamp.length; i++) {
            expect(Math.abs(at(onRamp, i) - at(onRamp, i - 1))).toBeLessThanOrEqual(1);
        }
    });

    it("paints an unknown span flat grey with hard edges, never a blend", () => {
        // One sample with no usable rain data greys the two spans that touch it - we know
        // nothing about the ground between its neighbours - while the rest keeps its colour.
        const stops = pairs(gradientStops([0, 0.25, 0.5, 0.75, 1], [0.1, 0.1, null, 0.1, 0.1]));
        const colors = stops.map(s => s.color);

        expect(colors).toContain(NO_DATA_COLOR);
        expect(colors.filter(c => c !== NO_DATA_COLOR).length).toBeGreaterThan(0);

        // The grey is one contiguous run: the line does not flicker in and out of it.
        const firstGrey = colors.indexOf(NO_DATA_COLOR);
        const lastGrey = colors.lastIndexOf(NO_DATA_COLOR);
        expect(colors.slice(firstGrey, lastGrey + 1).every(c => c === NO_DATA_COLOR)).toBe(true);

        // Both edges are hard - grey meets colour within one epsilon, so maplibre has no
        // room to blend them into a "partly known" shade.
        expect(at(stops, firstGrey).p - at(stops, firstGrey - 1).p).toBeLessThan(0.01);
        expect(at(stops, lastGrey + 1).p - at(stops, lastGrey).p).toBeLessThan(0.01);

        // Nothing between the known colour and the grey ever appears.
        const known = scoreColor(0.1);
        for (const c of colors) expect(c === NO_DATA_COLOR || c === known).toBe(true);
    });

    it("falls back sanely with no or one usable sample", () => {
        expect(gradientStops([], [])).toEqual([0, NO_DATA_COLOR, 1, NO_DATA_COLOR]);
        expect(gradientStops([0.3], [0])).toEqual([0, at(YLORRD_8, 0), 1, at(YLORRD_8, 0)]);
        const allNull = pairs(gradientStops([0, 0.5, 1], [null, null, null]));
        expect(allNull.every(s => s.color === NO_DATA_COLOR)).toBe(true);
    });

    it("anchors both ends of the line", () => {
        const stops = pairs(gradientStops([0.2, 0.8], [0.1, 0.4]));
        expect(at(stops, 0).p).toBe(0);
        expect(at(stops, stops.length - 1).p).toBe(1);
    });

    it("colours a served sample list end to end", () => {
        const samples = [
            sample({ lon: 0, lat: 0, rideScore: 0 }),
            sample({ lon: 0.01, lat: 0, rideScore: 0.6 }),
            sample({ lon: 0.02, lat: 0, rideScore: null }),
        ];
        const line = [
            [0, 0],
            [0.01, 0],
            [0.02, 0],
        ];
        const stops = pairs(gradientStops(sampleProgress(line, samples, 900), scoresOf(samples)));
        expect(at(stops, 0).color).toBe(at(YLORRD_8, 0));
        expect(at(stops, stops.length - 1).color).toBe(NO_DATA_COLOR);
    });
});

describe("scoreBand", () => {
    it("splits the range into the five label bands", () => {
        expect(scoreBand(0)).toBe(0);
        expect(scoreBand(0.19)).toBe(0);
        expect(scoreBand(0.2)).toBe(1);
        expect(scoreBand(0.5)).toBe(2);
        expect(scoreBand(1)).toBe(4);
    });

    it("has no band for an unknown score", () => {
        expect(scoreBand(null)).toBeNull();
        expect(scoreBand(Number.NaN)).toBeNull();
    });
});
