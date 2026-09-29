import type { ElevationOut } from "@norain/api/models";
import type { MapPoi } from "@/utils/poiCategories";
import { RouteForecastOutFromJSON } from "@norain/api/models";

// Simplified, hand-authored lakeside geometry, for illustration rather than navigation.
// No current forecast, personal account, routing server or paid weather API is involved.
export const line = [
    [6.8412, 46.4595],
    [6.8443, 46.4587],
    [6.8468, 46.4578],
    [6.8501, 46.4565],
    [6.853, 46.4551],
    [6.8565, 46.4538],
    [6.8607, 46.4523],
    [6.864, 46.4513],
    [6.8671, 46.4502],
    [6.8705, 46.4488],
    [6.8746, 46.4475],
    [6.8791, 46.4465],
    [6.8835, 46.4451],
    [6.8881, 46.444],
    [6.8921, 46.4427],
    [6.8958, 46.441],
    [6.8988, 46.4389],
    [6.9008, 46.436],
    [6.903, 46.4334],
    [6.9062, 46.4318],
    [6.9094, 46.4313],
];
const eta = (minutes: number) => `2026-06-15T08:${String(minutes).padStart(2, "0")}:00+02:00`;
const temperatures = [
    18, 18.2, 18.5, 18.8, 19, 18.8, 18.4, 17.9, 17.6, 17.3, 17, 17.2, 17.8, 18.3, 18.7, 19.2, 19.6, 20, 19.8, 19.6,
    19.4,
];
const sections = [
    {
        start_km: 0,
        end_km: 2.4,
        start_time: "08:00",
        end_time: "08:08",
        condition: "dry",
        max_rain_mm: 0,
        temp_min: 18,
        temp_max: 19,
    },
    {
        start_km: 2.4,
        end_km: 4.2,
        start_time: "08:08",
        end_time: "08:14",
        condition: "rain",
        max_rain_mm: 0.2,
        temp_min: 17,
        temp_max: 18,
    },
    {
        start_km: 4.2,
        end_km: 7.2,
        start_time: "08:14",
        end_time: "08:24",
        condition: "dry",
        max_rain_mm: 0,
        temp_min: 18,
        temp_max: 20,
    },
];
export const forecast = RouteForecastOutFromJSON({
    line,
    total_seconds: 1440,
    total_distance_m: 7200,
    departure_time: eta(0),
    sections,
    samples: line.map(([lon, lat], i) => {
        const wet = i >= 7 && i <= 11;
        return {
            lon,
            lat,
            elapsed_s: i * 72,
            eta: eta(Math.round(i * 1.2)),
            temp: temperatures[i] ?? 19,
            felt_temp: (temperatures[i] ?? 19) - 1.5,
            rain_mm: wet ? 0.2 : 0,
            precipitation_interval_s: 900,
            rain_rate_mm_h: wet ? 0.8 : 0,
            pop: wet ? 0.65 : 0.1,
            wind_speed: 12,
            wind_dir: 240,
            headwind: 4,
            crosswind: 9,
            weather_code: wet ? 61 : 2,
            ride_score: wet ? 0.45 : 0.12,
        };
    }),
    summary: {
        will_rain: true,
        first_rain_eta: eta(8),
        max_rain_mm: 0.2,
        rain_probability: 0.65,
        rain_amount: 0.8,
        max_headwind: 12,
        source: "open-meteo",
        wind_distribution: {
            headwind_m: 1400,
            crosswind_m: 3300,
            tailwind_m: 2500,
            calm_m: 0,
            unknown_m: 0,
            mean_felt_speed: 22,
            max_felt_speed: 30,
            felt_covered_m: 7200,
            timing_source: "routing",
        },
    },
});

// Deliberately illustrative elevations and stops, not surveyed heights or verified amenities.
export const elevation: ElevationOut = {
    points: [
        378, 380, 383, 382, 388, 396, 401, 398, 394, 390, 386, 389, 395, 402, 405, 400, 393, 387, 383, 380, 378,
    ].map((elevationM, i) => ({
        distanceM: i * 360,
        elapsedS: i * 72,
        elevationM,
    })),
    source: "Demo",
    approximateTiming: false,
};
export const pois: MapPoi[] = [
    { osmRef: "demo-water", lon: 6.8607, lat: 46.4523, category: "drinking_water", planned: true },
    { osmRef: "demo-food", lon: 6.8921, lat: 46.4427, category: "food", planned: true },
];
export const schedule = { days: [1, 2, 3, 4, 5], outward: "08:00", return: "17:00" };
