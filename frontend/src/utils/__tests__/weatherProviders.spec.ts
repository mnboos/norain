import { describe, expect, it } from "vitest";
import { weatherProviders } from "../weatherProviders";

describe("weatherProviders", () => {
    it("names each provider once, in the order the forecast used them", () => {
        const names = weatherProviders({
            source: "open-meteo",
            sources: ["open-meteo", "met-norway", "open-meteo-ensemble"],
        }).map(p => p.name);
        expect(names).toEqual(["Open-Meteo", "MET Norway"]);
    });

    it("falls back to the single source of an older result", () => {
        expect(weatherProviders({ source: "openweathermap" }).map(p => p.name)).toEqual(["OpenWeatherMap"]);
        expect(weatherProviders({ source: "met-norway", sources: [] }).map(p => p.url)).toEqual([
            "https://www.met.no/en",
        ]);
    });

    it("ignores sources it does not know", () => {
        expect(weatherProviders({ source: "somewhere" })).toEqual([]);
    });
});
