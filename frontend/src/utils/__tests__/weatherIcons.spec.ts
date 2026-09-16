import { describe, expect, it } from "vitest";

import { FROST_MARK, isNightEta, pickVisibleSamples, rainCondition, weatherGlyphName, weatherIconSvg } from "@/utils/weatherIcons";

// Every code the backend's WMO_DE table (core/weather.py) can produce.
const WMO_CODES = [
    0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99,
];

describe("rainCondition", () => {
    it("uses the same thresholds as the backend sections", () => {
        expect(rainCondition(0)).toBe("dry");
        expect(rainCondition(0.09)).toBe("dry");
        expect(rainCondition(0.1)).toBe("rain");
        expect(rainCondition(2.49)).toBe("rain");
        expect(rainCondition(2.5)).toBe("heavy_rain");
    });
});

describe("isNightEta", () => {
    it("reads the hour off the ISO string, offsets included", () => {
        expect(isNightEta("2026-09-10T08:30:00")).toBe(false);
        expect(isNightEta("2026-09-10T22:30:00+02:00")).toBe(true);
        expect(isNightEta("2026-09-10T03:00:00")).toBe(true);
        expect(isNightEta("nonsense")).toBe(false);
    });
});

describe("weatherIconSvg", () => {
    it("returns markup for every WMO code the backend knows", () => {
        for (const code of WMO_CODES) {
            expect(weatherIconSvg(code, { rainMm: 0, night: false }).trim()).not.toBe("");
        }
    });

    it("falls back to the rain amount when the code is missing (OpenWeatherMap path)", () => {
        const dry = weatherIconSvg(null, { rainMm: 0, night: false });
        const wet = weatherIconSvg(undefined, { rainMm: 3, night: false });
        expect(dry.trim()).not.toBe("");
        expect(wet.trim()).not.toBe("");
        expect(wet).not.toBe(dry);
    });

    it("has an ice crystal to mark frost with, separate from every sky glyph", () => {
        // Frost is what the road does; the glyph says what the sky does. The chip shows both,
        // so a cold clear morning keeps its sun (see NiceMap `wx-frost`).
        expect(FROST_MARK.trim()).not.toBe("");
        for (const code of WMO_CODES) {
            expect(weatherIconSvg(code, { rainMm: 0, night: false })).not.toBe(FROST_MARK);
        }
        expect(weatherGlyphName(0, { rainMm: 0, night: false })).toBe("clear_day");
    });

    it("shows a moon at night but only when it is clear", () => {
        expect(weatherIconSvg(0, { rainMm: 0, night: true })).not.toBe(weatherIconSvg(0, { rainMm: 0, night: false }));
        expect(weatherIconSvg(63, { rainMm: 1, night: true })).toBe(weatherIconSvg(63, { rainMm: 1, night: false }));
    });
});

describe("pickVisibleSamples", () => {
    it("keeps a chip where the ride-quality band changes, even with flat rain", () => {
        // Rain never changes condition, so only the band marks the transition - this is the
        // case where wind or cold turns the line's colour with nothing else to signal it.
        const samples = Array.from({ length: 21 }, (_, i) => ({ rainMm: 0, band: i < 10 ? 0 : 3 }));
        const kept = pickVisibleSamples(samples, i => ({ x: i * 30, y: 0 }), 64, 24);
        expect(kept.has(10)).toBe(true);

        // Without the band there is nothing to hold that chip at this spacing.
        const flat = samples.map(s => ({ rainMm: s.rainMm }));
        expect(pickVisibleSamples(flat, i => ({ x: i * 30, y: 0 }), 64, 24).has(10)).toBe(false);
    });

    it("keeps a chip where the road turns icy", () => {
        // Same reason as the band: a frosty stretch must not lose the only mark that says so.
        const samples = Array.from({ length: 21 }, (_, i) => ({
            rainMm: 0,
            frost: i < 10 ? null : "stark",
        }));
        const kept = pickVisibleSamples(samples, i => ({ x: i * 30, y: 0 }), 64, 24);
        expect(kept.has(10)).toBe(true);

        const flat = samples.map(s => ({ rainMm: s.rainMm }));
        expect(pickVisibleSamples(flat, i => ({ x: i * 30, y: 0 }), 64, 24).has(10)).toBe(false);
    });

    /** Samples laid out on a horizontal line, `gap` pixels apart. */
    const row = (gap: number) => (i: number) => ({ x: i * gap, y: 0 });

    it("handles an empty route", () => {
        expect(pickVisibleSamples([], row(10)).size).toBe(0);
    });

    it("always keeps the first and last sample", () => {
        const samples = Array.from({ length: 10 }, () => ({ rainMm: 0 }));
        const kept = pickVisibleSamples(samples, row(1));
        expect(kept.has(0)).toBe(true);
        expect(kept.has(9)).toBe(true);
    });

    it("thins evenly spaced samples to the minimum spacing", () => {
        const samples = Array.from({ length: 21 }, () => ({ rainMm: 0 }));
        const kept = pickVisibleSamples(samples, row(10), 40);
        // 200 px of route at 40 px spacing -> roughly every 4th sample, not all 21.
        expect(kept.size).toBeLessThanOrEqual(7);
        expect(kept.size).toBeGreaterThan(2);
    });

    it("keeps a short rain window that even spacing would have skipped", () => {
        const samples = Array.from({ length: 21 }, (_, i) => ({ rainMm: i === 5 ? 1.5 : 0 }));
        const kept = pickVisibleSamples(samples, row(10), 400);
        expect(kept.has(5)).toBe(true);
    });

    it("still thins hard when the condition alternates every sample", () => {
        // Showery weather: dry/rain/dry/rain... every sample is a condition change.
        const samples = Array.from({ length: 40 }, (_, i) => ({ rainMm: i % 2 === 0 ? 0 : 1 }));
        const kept = pickVisibleSamples(samples, row(4), 64, 24);
        // 4 px apart, 24 px floor -> at most every 6th sample survives.
        expect(kept.size).toBeLessThanOrEqual(9);
    });
});
