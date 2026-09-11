import type { EnsembleRange, RouteWeatherOut, RouteWeatherSummary, WeatherSample } from "@norain/api";

export function swissTime(iso: string): string {
    // Provider/route timestamps without an offset are already Swiss local time.
    if (!/(Z|[+-]\d\d:\d\d)$/.test(iso)) return iso.slice(11, 16);
    return new Date(iso).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Zurich" });
}

export function forecastHeadline(summary: RouteWeatherSummary, samples: WeatherSample[]): string {
    if (!samples.length) return "Keine Wetterdaten";
    const p = summary.rainProbability;
    if (p == null) return summary.willRain ? "Regen in der Einzelprognose" : "Kein Regen in der Einzelprognose";
    if (p === 0) {
        return samples.some(s => s.probabilitySource !== "open-meteo-ensemble" && s.pop != null)
            ? "An verfügbaren Punkten: 0% Regenrisiko"
            : "Keine nassen Ensemble-Mitglieder an verfügbaren Punkten";
    }
    const time = summary.firstRainEta ? ` ab ca. ${swissTime(summary.firstRainEta)} Uhr` : "";
    return `Regen möglich${time}`;
}

export function peakRisk(forecast: RouteWeatherOut): string {
    const p = forecast.summary.rainProbability;
    return p == null ? "Keine Wahrscheinlichkeitsdaten" : `${Math.round(p * 100)}%`;
}

export function rangeText(range: EnsembleRange | undefined, unit: string): string {
    if (range?.median == null || range.p10 == null || range.p90 == null) return "Nicht verfügbar";
    return `${range.p10.toFixed(1)}–${range.p90.toFixed(1)} ${unit} (Median ${range.median.toFixed(1)})`;
}

export const metricLabels = [
    { key: "precipitation", label: "Niederschlag", unit: "mm/h" },
    { key: "temperature", label: "Temperatur", unit: "°C" },
    { key: "windSpeed", label: "Wind", unit: "km/h" },
    { key: "windGust", label: "Böen", unit: "km/h" },
    { key: "headwind", label: "Gegen-/Rückenwind", unit: "km/h" },
    { key: "crosswind", label: "Seitenwind", unit: "km/h" },
];

const modelLabels: Record<string, string> = {
    icon_seamless_eps: "DWD ICON Seamless",
    meteoswiss_icon_ch1_ensemble: "MeteoSwiss ICON CH1",
    meteoswiss_icon_ch2_ensemble: "MeteoSwiss ICON CH2",
};

export function modelLabel(model: string): string {
    return modelLabels[model] ?? model;
}
