import { describe, expect, it } from "vitest";
import { forecastHeadline, peakRisk, rangeText, swissTime } from "../forecastDetails";
import { RouteWeatherOutFromJSON } from "@norain/api";

const wireSample = {
    lat: 47.5,
    lon: 9.5,
    elapsed_s: 0,
    eta: "2026-09-10T12:00",
    rain_mm: 0,
    temp: 15,
    wind_speed: 10,
    wind_dir: 0,
    headwind: 10,
    crosswind: 0,
    weather_desc: "Klar",
    probability_source: "open-meteo-ensemble",
    rain_rate_mm_h: 0,
    precipitation_interval_s: 900,
    pop: 0.25,
    uncertainty: {
        metrics: { temperature: { member_count: 2, p10: 12, median: 15, p90: 18 } },
        models: [],
        requested_models: ["a"],
        forecast_time: "2026-09-10T12:00:00+02:00",
        fetched_at: "2026-09-10T09:00:00Z",
    },
};
function forecast(pop: number | null) {
    return RouteWeatherOutFromJSON({
        line: [],
        total_seconds: 0,
        total_distance_m: 0,
        samples: [wireSample],
        summary: {
            will_rain: pop != null && pop >= 0.25,
            rain_probability: pop,
            first_rain_eta: "2026-09-10T12:00",
            max_rain_mm: 0,
            rain_amount: 0,
            max_headwind: 10,
            source: "open-meteo",
        },
    });
}

describe("forecast uncertainty presentation", () => {
    it("roundtrips nested API statistics and metadata", () => {
        const value = forecast(0.25).samples[0]!;
        expect(value.uncertainty?.metrics.temperature?.memberCount).toBe(2);
        expect(value.uncertainty?.forecastTime).toBe("2026-09-10T12:00:00+02:00");
        expect(value.probabilitySource).toBe("open-meteo-ensemble");
        expect(value.rainRateMmH).toBe(0);
    });
    it("does not describe 25% as likely or hide a lower nonzero risk", () => {
        for (const p of [0.25, 0.1]) {
            const f = forecast(p);
            expect(forecastHeadline(f.summary, f.samples)).toContain("Regen möglich");
            expect(peakRisk(f)).toBe(`${p * 100}%`);
        }
    });
    it("distinguishes unavailable, zero and empty forecasts", () => {
        const f = forecast(null);
        expect(peakRisk(f)).toBe("Keine Wahrscheinlichkeitsdaten");
        expect(forecastHeadline(f.summary, [])).toBe("Keine Wetterdaten");
        f.summary.rainProbability = 0;
        expect(forecastHeadline(f.summary, f.samples)).toContain("Keine nassen Ensemble-Mitglieder");
        f.samples[0]!.probabilitySource = "openweathermap";
        expect(forecastHeadline(f.summary, f.samples)).toContain("0% Regenrisiko");
    });
    it("does not invent ranges for one member", () => {
        expect(rangeText({ memberCount: 1 }, "°C")).toBe("Nicht verfügbar");
        expect(rangeText({ memberCount: 2, p10: 0, median: 0, p90: 0 }, "mm/h")).toContain("0.0–0.0");
    });
    it("renders Swiss local time independently of browser timezone", () => {
        expect(swissTime("2026-09-10T12:00")).toBe("12:00");
        expect(swissTime("2026-09-10T10:00:00Z")).toBe("12:00");
    });
});
