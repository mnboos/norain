import { describe, expect, it } from "vitest";

import type { WeatherSample } from "@norain/api/models";
import {
    NO_DATA_COLOR,
    SPECTRAL_10,
    gradientStops,
    rideScore,
    rideScoreLabel,
    sampleProgress,
    scoreBand,
    scoreColor,
} from "@/utils/rideQuality";

function sample(over: Partial<WeatherSample> = {}): WeatherSample {
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

/** Score of a sample, or null when it has no usable data - what gradientStops consumes. */
const scoresOf = (ss: WeatherSample[]) => ss.map(s => rideScore(s)?.score ?? null);

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

describe("rideScore", () => {
    it("is 0 for a dry, mild, tailwind ride and 1 for the worst case", () => {
        expect(rideScore(sample({ rainRateMmH: 0, temp: 18, headwind: -12 }))?.score).toBe(0);
        expect(rideScore(sample({ rainRateMmH: 8, temp: -10, headwind: 45 }))?.score).toBe(1);
    });

    it("rises monotonically with rain", () => {
        const at_ = (mm: number) => rideScore(sample({ rainRateMmH: mm }))?.score ?? Number.NaN;
        const series = [0, 0.2, 1, 2.5, 5, 9].map(at_);
        for (let i = 1; i < series.length; i++) expect(at(series, i)).toBeGreaterThanOrEqual(at(series, i - 1));
        expect(at_(3)).toBeGreaterThan(at_(0.5));
    });

    it("rises monotonically with headwind", () => {
        const at_ = (kmh: number) => rideScore(sample({ headwind: kmh }))?.score ?? Number.NaN;
        const series = [-20, 0, 5, 10, 20, 30, 50].map(at_);
        for (let i = 1; i < series.length; i++) expect(at(series, i)).toBeGreaterThanOrEqual(at(series, i - 1));
    });

    it("rises as the temperature leaves the comfortable band in either direction", () => {
        const at_ = (c: number) => rideScore(sample({ temp: c }))?.score ?? Number.NaN;
        expect(at_(18)).toBe(0);
        expect(at_(14)).toBe(0);
        expect(at_(22)).toBe(0);
        expect(at_(5)).toBeGreaterThan(at_(12));
        expect(at_(30)).toBeGreaterThan(at_(24));
        expect(at_(-10)).toBeGreaterThan(at_(0));
    });

    it("does not reward a tailwind - it only removes the penalty", () => {
        expect(rideScore(sample({ headwind: -30 }))?.score).toBe(rideScore(sample({ headwind: 0 }))?.score);
    });

    it("names the dominant weighted factor", () => {
        expect(rideScore(sample({ rainRateMmH: 4 }))?.worst).toBe("rain");
        expect(rideScore(sample({ rainRateMmH: 0, headwind: 28 }))?.worst).toBe("wind");
        expect(rideScore(sample({ rainRateMmH: 0, temp: -5 }))?.worst).toBe("temp");
    });
});

describe("rideScore missing data", () => {
    it("derives the rate from the accumulation when the interval is known", () => {
        // 0.5 mm over 15 min == 2 mm/h
        const derived = rideScore(sample({ rainRateMmH: null, rainMm: 0.5, precipitationIntervalS: 900 }));
        expect(derived?.rain).toBeCloseTo(rideScore(sample({ rainRateMmH: 2 }))?.rain ?? Number.NaN, 10);
    });

    it("returns null rather than assuming an hourly bucket", () => {
        expect(rideScore(sample({ rainRateMmH: null, rainMm: 0.5, precipitationIntervalS: null }))).toBeNull();
        expect(rideScore(sample({ rainRateMmH: null, rainMm: 0.5, precipitationIntervalS: 0 }))).toBeNull();
    });

    it("only names a cause when one factor actually dominates", () => {
        // Rain alone: worth naming.
        expect(rideScoreLabel(rideScore(sample({ rainRateMmH: 3 })))).toContain("v. a. Regen");
        // Drizzle, headwind and cold all pulling together: the largest weighted term is
        // wind, but at well under half the total it is not an honest culprit, so the label
        // reports the band alone rather than blaming one factor.
        const mixed = rideScore(sample({ rainRateMmH: 0.5, headwind: 20, temp: 8 }));
        expect(mixed?.worst).toBe("wind");
        expect(rideScoreLabel(mixed)).not.toContain("v. a.");
        expect(rideScoreLabel(mixed)).toBe("gut");
    });

    it("labels an unknown score instead of inventing a band", () => {
        expect(rideScoreLabel(null)).toBe("Nicht verfügbar");
        expect(rideScoreLabel(rideScore(sample()))).toBe("sehr gut");
        expect(rideScoreLabel(rideScore(sample({ rainRateMmH: 6 })))).toContain("Regen");
    });
});

describe("scoreColor", () => {
    it("hits the exact Spectral endpoints", () => {
        expect(scoreColor(0)).toBe(at(SPECTRAL_10, 0));
        expect(scoreColor(1)).toBe(at(SPECTRAL_10, SPECTRAL_10.length - 1));
    });

    it("lands on the intermediate steps and interpolates between them", () => {
        expect(scoreColor(1 / 9)).toBe(at(SPECTRAL_10, 1));
        expect(scoreColor(4 / 9)).toBe(at(SPECTRAL_10, 4));
        const mid = scoreColor(0.5 / 9);
        expect(mid).not.toBe(at(SPECTRAL_10, 0));
        expect(mid).not.toBe(at(SPECTRAL_10, 1));
    });

    it("returns the neutral grey - never a Spectral step - for unknown values", () => {
        for (const v of [null, undefined, Number.NaN]) expect(scoreColor(v)).toBe(NO_DATA_COLOR);
        // Widened on purpose: as literal types TS can already prove these never overlap,
        // and that proof is the point - the grey must never collide with a ramp step.
        const ramp: readonly string[] = SPECTRAL_10;
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

    it("subdivides a big jump so the ramp passes through Spectral instead of chording across it", () => {
        // 0.15 -> 0.75 is more than five Spectral steps. One stop per sample would hand
        // maplibre #3288bd and #d53e4f and let it blend the chord between them, which stays
        // muddy the whole way - it never goes green.
        const stops = pairs(gradientStops([0, 1], [0.15, 0.75]));
        expect(stops.length).toBeGreaterThan(5);

        // The real ramp passes through the green/yellow middle of Spectral; the chord cannot.
        const greenDominant = stops.filter(s => {
            const [r, g, b] = channels(s.color);
            return g > r && g > b;
        });
        expect(greenDominant.length).toBeGreaterThan(0);

        // ...and it gets there gradually: no visible lurch between neighbouring stops.
        for (let i = 1; i < stops.length; i++) {
            const [r0, g0, b0] = channels(at(stops, i - 1).color);
            const [r1, g1, b1] = channels(at(stops, i).color);
            expect(Math.max(Math.abs(r1 - r0), Math.abs(g1 - g0), Math.abs(b1 - b0))).toBeLessThan(70);
        }
    });

    it("never lets two adjacent known stops span more than one Spectral step", () => {
        const stops = pairs(gradientStops([0, 0.4, 1], [0, 1, 0.3]));
        const onRamp = stops.map(s => SPECTRAL_10.findIndex(c => c === s.color)).filter(i => i >= 0);
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
        expect(gradientStops([0.3], [0])).toEqual([0, at(SPECTRAL_10, 0), 1, at(SPECTRAL_10, 0)]);
        const allNull = pairs(gradientStops([0, 0.5, 1], [null, null, null]));
        expect(allNull.every(s => s.color === NO_DATA_COLOR)).toBe(true);
    });

    it("anchors both ends of the line", () => {
        const stops = pairs(gradientStops([0.2, 0.8], [0.1, 0.4]));
        expect(at(stops, 0).p).toBe(0);
        expect(at(stops, stops.length - 1).p).toBe(1);
    });

    it("scores a real sample list end to end", () => {
        const samples = [
            sample({ lon: 0, lat: 0, rainRateMmH: 0 }),
            sample({ lon: 0.01, lat: 0, rainRateMmH: 3, headwind: 20 }),
            sample({ lon: 0.02, lat: 0, rainRateMmH: null, precipitationIntervalS: null }),
        ];
        const line = [
            [0, 0],
            [0.01, 0],
            [0.02, 0],
        ];
        const stops = pairs(gradientStops(sampleProgress(line, samples, 900), scoresOf(samples)));
        expect(at(stops, 0).color).toBe(at(SPECTRAL_10, 0));
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
