import type { EnsembleRange, RouteForecastOut, RouteWeatherSummary, ForecastSampleOut } from "@norain/api/models";

export function swissTime(iso: string): string {
    // Provider/route timestamps without an offset are already Swiss local time.
    if (!/(Z|[+-]\d\d:\d\d)$/.test(iso)) return iso.slice(11, 16);
    return new Date(iso).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Zurich" });
}

// Largest rain rate of the deterministic main run, rounded the way the card displays it.
function mainRunPeakRate(samples: ForecastSampleOut[]): number | null {
    const rates = samples.flatMap(s => (s.rainRateMmH == null ? [] : [s.rainRateMmH]));
    return rates.length ? Math.round(Math.max(...rates) * 10) / 10 : null;
}

export function forecastHeadline(summary: RouteWeatherSummary, samples: ForecastSampleOut[]): string {
    if (!samples.length) return "Keine Wetterdaten";
    const p = summary.rainProbability;
    if (p == null) return summary.willRain ? "Regen erwartet" : "Voraussichtlich trocken";
    // `willRain` is the backend's ensemble verdict (POP_VERDICT). A few wet members below it
    // are shown as the risk percentage, not as a headline next to a dry "Regen max.". The
    // main run raining still counts, or the headline would say dry beside a non-zero rate.
    if (!summary.willRain && !(mainRunPeakRate(samples) ?? 0)) return "Voraussichtlich trocken";
    const time = summary.firstRainEta ? ` ab ca. ${swissTime(summary.firstRainEta)} Uhr` : "";
    return `Regen möglich${time}`;
}

/**
 * The "Regen max." figure, from the same forecast as the headline: when the ensemble expects
 * rain, the amount its wet members predict (`rainAmount`, mm/h at the peak-risk point) —
 * the main run is a single scenario and is often dry exactly where the members are not.
 */
export function peakRain(forecast: RouteForecastOut): string | null {
    const { summary, samples } = forecast;
    const mainRun = mainRunPeakRate(samples);
    if (summary.rainProbability != null && summary.willRain) {
        return Math.max(summary.rainAmount, mainRun ?? 0).toFixed(1);
    }
    return mainRun == null ? null : mainRun.toFixed(1);
}

export function peakRisk(forecast: RouteForecastOut): string {
    const p = forecast.summary.rainProbability;
    return p == null ? "Nicht verfügbar" : `${Math.round(p * 100)}%`;
}

export function rangeText(range: EnsembleRange | undefined, unit: string): string {
    if (range?.median == null || range.p10 == null || range.p90 == null) return "Nicht verfügbar";
    return `${range.p10.toFixed(1)}–${range.p90.toFixed(1)} ${unit}`;
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
