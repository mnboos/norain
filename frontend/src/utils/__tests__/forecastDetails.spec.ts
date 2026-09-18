import { describe, expect, it } from "vitest";
import { forecastHeadline, peakRain, peakRisk, rangeText, swissTime } from "../forecastDetails";
import { RouteForecastOutFromJSON } from "@norain/api/models";

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
        forecast_time: "2026-09-10T12:00:00+02:00",
        fetched_at: "2026-09-10T09:00:00Z",
    },
};
interface ForecastOptions {
    rainAmount?: number;
    rainRate?: number;
    windLevel?: string | null;
    frostLevel?: string | null;
}
function forecast(
    pop: number | null,
    { rainAmount = 0, rainRate = 0, windLevel = null, frostLevel = null }: ForecastOptions = {},
) {
    return RouteForecastOutFromJSON({
        job_id: "00000000-0000-0000-0000-000000000001",
        version: "2026-09-10T09:00:00+00:00",
        departure_time: "2026-09-10T12:00",
        line: [],
        total_seconds: 0,
        total_distance_m: 0,
        samples: [{ ...wireSample, rain_rate_mm_h: rainRate }],
        summary: {
            will_rain: pop != null && pop >= 0.25,
            rain_probability: pop,
            first_rain_eta: pop != null && pop >= 0.25 ? "2026-09-10T12:00" : null,
            max_rain_mm: 0,
            rain_amount: rainAmount,
            max_headwind: 10,
            max_wind_effort_level: windLevel,
            max_frost_level: frostLevel,
            source: "open-meteo",
        },
    });
}

describe("forecast uncertainty presentation", () => {
    it("roundtrips nested API statistics and metadata", () => {
        const value = forecast(0.25).samples[0];
        expect(value?.uncertainty?.metrics.temperature?.memberCount).toBe(2);
        expect(value?.uncertainty?.forecastTime).toBe("2026-09-10T12:00:00+02:00");
        expect(value?.probabilitySource).toBe("open-meteo-ensemble");
        expect(value?.rainRateMmH).toBe(0);
    });
    it("says rain is possible from the ensemble verdict, not from any wet member", () => {
        const likely = forecast(0.25);
        expect(forecastHeadline(likely.summary, likely.samples)).toBe("Regen möglich ab ca. 12:00 Uhr");
        const unlikely = forecast(0.1);
        expect(forecastHeadline(unlikely.summary, unlikely.samples)).toBe("Voraussichtlich trocken");
        // The low risk stays visible as a number.
        expect(peakRisk(unlikely)).toBe("10%");
    });
    it("does not call a route dry while the main run rains", () => {
        const f = forecast(0.1, { rainRate: 0.5 });
        expect(forecastHeadline(f.summary, f.samples)).toContain("Regen möglich");
        expect(peakRain(f)).toBe("0.5");
    });
    it("names the wind when the ride is dry", () => {
        const headline = (windLevel: string | null, pop: number | null = 0) => {
            const f = forecast(pop, { windLevel });
            return forecastHeadline(f.summary, f.samples);
        };
        expect(headline("mittel")).toBe("Trocken, etwas Gegenwind");
        expect(headline("hoch")).toBe("Trocken, starker Gegenwind");
        expect(headline("sehr hoch")).toBe("Trocken, sehr starker Gegenwind");
        expect(headline("Wind hilft")).toBe("Trocken mit Rückenwind");
        expect(headline("niedrig")).toBe("Voraussichtlich trocken");
        expect(headline("keiner")).toBe("Voraussichtlich trocken");
        // No ensemble: the dry verdict still names the wind.
        expect(headline("hoch", null)).toBe("Trocken, starker Gegenwind");
    });
    it("puts frost before wind on a dry ride", () => {
        const f = forecast(0, { windLevel: "hoch", frostLevel: "mässig" });
        expect(forecastHeadline(f.summary, f.samples)).toBe("Trocken, aber Glättegefahr");
        const light = forecast(0, { windLevel: "hoch", frostLevel: "leicht" });
        expect(forecastHeadline(light.summary, light.samples)).toBe("Trocken, leichte Glättegefahr");
    });
    it("keeps the rain headline when rain is possible, whatever the wind", () => {
        const f = forecast(0.25, { windLevel: "sehr hoch", frostLevel: "stark" });
        expect(forecastHeadline(f.summary, f.samples)).toBe("Regen möglich ab ca. 12:00 Uhr");
    });
    it("shows the wet members' amount when rain is expected", () => {
        expect(peakRain(forecast(0.4, { rainAmount: 1.2 }))).toBe("1.2");
        expect(peakRain(forecast(0.4, { rainAmount: 1.2, rainRate: 2 }))).toBe("2.0");
        expect(peakRain(forecast(0.1, { rainAmount: 1.2 }))).toBe("0.0");
        expect(peakRain(forecast(null, { rainAmount: 1.2, rainRate: 0.3 }))).toBe("0.3");
    });
    it("distinguishes unavailable, zero and empty forecasts", () => {
        const f = forecast(null);
        expect(peakRisk(f)).toBe("Nicht verfügbar");
        expect(forecastHeadline(f.summary, [])).toBe("Keine Wetterdaten");
        f.summary.rainProbability = 0;
        expect(forecastHeadline(f.summary, f.samples)).toContain("Voraussichtlich trocken");
        const sample = f.samples[0];
        if (!sample) throw new Error("Fixture must contain a weather sample.");
        sample.probabilitySource = "openweathermap";
        expect(forecastHeadline(f.summary, f.samples)).toContain("Voraussichtlich trocken");
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
